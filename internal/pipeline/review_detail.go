package pipeline

import (
	"bytes"
	"compress/gzip"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"hash"
	"io"
	"os"
	"path/filepath"
	"sort"
	"strconv"

	"terraria-resource-pipeline/internal/artifact"
)

// ReviewFieldValue omits a side when the field did not exist. A present JSON
// null is represented by a pointer to the literal null instead.
type ReviewFieldValue struct {
	Before *json.RawMessage `json:"before,omitempty"`
	After  *json.RawMessage `json:"after,omitempty"`
}

// ReviewDetail is one line of review-changes.ndjson, ordered by family then ID.
// Row additions/removals include the entire row; changes include only fields.
type ReviewDetail struct {
	Domain        string                      `json:"domain"`
	ID            json.RawMessage             `json:"id"`
	Change        string                      `json:"change"`
	Before        json.RawMessage             `json:"before,omitempty"`
	After         json.RawMessage             `json:"after,omitempty"`
	Fields        map[string]ReviewFieldValue `json:"fields,omitempty"`
	TextureBefore *Texture                    `json:"textureBefore,omitempty"`
	TextureAfter  *Texture                    `json:"textureAfter,omitempty"`
	BeforeSHA256  string                      `json:"beforeSha256,omitempty"`
	AfterSHA256   string                      `json:"afterSha256,omitempty"`
}

type compactReviewedRow struct {
	hash      [32]byte
	pack, row int
}

func normalizedRow(raw json.RawMessage) (string, []byte, [32]byte, error) {
	var decoded map[string]any
	d := json.NewDecoder(bytes.NewReader(raw))
	d.UseNumber()
	if err := d.Decode(&decoded); err != nil {
		return "", nil, [32]byte{}, err
	}
	if decoded == nil {
		return "", nil, [32]byte{}, errors.New("resource row is not an object")
	}
	id, ok := decoded["id"]
	if !ok {
		return "", nil, [32]byte{}, errors.New("resource row lacks id")
	}
	idBytes, err := json.Marshal(id)
	if err != nil {
		return "", nil, [32]byte{}, err
	}
	canonical, err := json.Marshal(decoded)
	if err != nil {
		return "", nil, [32]byte{}, err
	}
	return string(idBytes), canonical, sha256.Sum256(canonical), nil
}

func openReviewPack(root string, object artifact.Object) (*os.File, *gzip.Reader, *json.Decoder, error) {
	if err := artifact.Verify(root, object); err != nil {
		return nil, nil, nil, err
	}
	file, err := os.Open(filepath.Join(root, filepath.FromSlash(object.Path)))
	if err != nil {
		return nil, nil, nil, err
	}
	var source io.Reader = file
	var zipper *gzip.Reader
	if object.Encoding == "gzip" {
		zipper, err = gzip.NewReader(file)
		if err != nil {
			file.Close()
			return nil, nil, nil, err
		}
		source = zipper
	}
	decoder := json.NewDecoder(source)
	token, err := decoder.Token()
	if err != nil {
		if zipper != nil {
			zipper.Close()
		}
		file.Close()
		return nil, nil, nil, err
	}
	if token != json.Delim('[') {
		if zipper != nil {
			zipper.Close()
		}
		file.Close()
		return nil, nil, nil, errors.New("family pack is not an array")
	}
	return file, zipper, decoder, nil
}

func scanReviewPack(root string, object artifact.Object, visit func(int, json.RawMessage) error) (int, error) {
	file, zipper, decoder, err := openReviewPack(root, object)
	if err != nil {
		return 0, err
	}
	defer file.Close()
	if zipper != nil {
		defer zipper.Close()
	}
	count := 0
	for decoder.More() {
		var raw json.RawMessage
		if err := decoder.Decode(&raw); err != nil {
			return 0, err
		}
		if err := visit(count, raw); err != nil {
			return 0, err
		}
		count++
	}
	token, err := decoder.Token()
	if err != nil {
		return 0, err
	}
	if token != json.Delim(']') {
		return 0, errors.New("family pack has invalid array ending")
	}
	return count, nil
}

func familyRows(root string, packs []Pack) (map[string]compactReviewedRow, error) {
	rows := make(map[string]compactReviewedRow)
	for packIndex, pack := range packs {
		if pack.Rows < 0 {
			return nil, errors.New("negative family row count")
		}
		count, err := scanReviewPack(root, pack.Object, func(rowIndex int, raw json.RawMessage) error {
			id, _, sum, err := normalizedRow(raw)
			if err != nil {
				return err
			}
			if _, exists := rows[id]; exists {
				return fmt.Errorf("duplicate family row id %s", id)
			}
			rows[id] = compactReviewedRow{hash: sum, pack: packIndex, row: rowIndex}
			return nil
		})
		if err != nil {
			return nil, err
		}
		if count != pack.Rows {
			return nil, errors.New("manifest family row count mismatch")
		}
	}
	return rows, nil
}

type reviewRowCache struct {
	root    string
	packs   []Pack
	stored  map[int]cachedReviewPack
	bytes   int
	clock   uint64
	index   int
	file    *os.File
	zipper  *gzip.Reader
	decoder *json.Decoder
	next    int
}

// Keep only a small, bounded set of decoded packs. Numeric detail order need
// not match pack order, so a sequential decoder can otherwise restart a pack
// (and rehash it) for nearly every row.
const reviewPackCacheBytes = 16 << 20

type cachedReviewPack struct {
	rows  []json.RawMessage
	bytes int
	last  uint64
}

func (c *reviewRowCache) close() {
	if c.zipper != nil {
		c.zipper.Close()
		c.zipper = nil
	}
	if c.file != nil {
		c.file.Close()
		c.file = nil
	}
	c.decoder = nil
}
func (c *reviewRowCache) row(ref compactReviewedRow) (json.RawMessage, error) {
	if ref.pack < 0 || ref.pack >= len(c.packs) {
		return nil, errors.New("invalid review row pack")
	}
	if ref.row < 0 || ref.row >= c.packs[ref.pack].Rows {
		return nil, errors.New("invalid review row offset")
	}
	c.clock++
	if cached, ok := c.stored[ref.pack]; ok {
		cached.last = c.clock
		c.stored[ref.pack] = cached
		return cached.rows[ref.row], nil
	}
	if c.packs[ref.pack].Object.Bytes <= 1<<20 {
		var rows []json.RawMessage
		rawBytes := 0
		size := 0
		tooLarge := errors.New("review pack exceeds cache")
		count, err := scanReviewPack(c.root, c.packs[ref.pack].Object, func(_ int, raw json.RawMessage) error {
			rawBytes += cap(raw)
			rows = append(rows, raw)
			size = rawBytes + cap(rows)*24
			if size > reviewPackCacheBytes {
				return tooLarge
			}
			return nil
		})
		if err == nil {
			if count != c.packs[ref.pack].Rows {
				return nil, errors.New("manifest family row count mismatch")
			}
			for c.bytes+size > reviewPackCacheBytes {
				var oldest int
				var oldestTime uint64 = ^uint64(0)
				for pack, cached := range c.stored {
					if cached.last < oldestTime {
						oldest, oldestTime = pack, cached.last
					}
				}
				c.bytes -= c.stored[oldest].bytes
				delete(c.stored, oldest)
			}
			if c.stored == nil {
				c.stored = make(map[int]cachedReviewPack)
			}
			c.stored[ref.pack] = cachedReviewPack{rows: rows, bytes: size, last: c.clock}
			c.bytes += size
			return rows[ref.row], nil
		}
		if !errors.Is(err, tooLarge) {
			return nil, err
		}
	}
	if c.index != ref.pack || ref.row < c.next {
		c.close()
		file, zipper, decoder, err := openReviewPack(c.root, c.packs[ref.pack].Object)
		if err != nil {
			return nil, err
		}
		c.file = file
		c.zipper = zipper
		c.decoder = decoder
		c.next = 0
		c.index = ref.pack
	}
	for c.next <= ref.row {
		if !c.decoder.More() {
			return nil, errors.New("review row pack changed")
		}
		var raw json.RawMessage
		if err := c.decoder.Decode(&raw); err != nil {
			return nil, err
		}
		c.next++
		if c.next-1 == ref.row {
			return raw, nil
		}
	}
	return nil, errors.New("review row seek failed")
}

func sortedReviewIDs[T any](before, after map[string]T) []string {
	seen := make(map[string]bool, len(before)+len(after))
	keys := make([]string, 0, len(before)+len(after))
	for id := range before {
		seen[id] = true
		keys = append(keys, id)
	}
	for id := range after {
		if !seen[id] {
			keys = append(keys, id)
		}
	}
	sort.Slice(keys, func(i, j int) bool {
		a, ea := strconv.ParseInt(keys[i], 10, 64)
		b, eb := strconv.ParseInt(keys[j], 10, 64)
		if ea == nil && eb == nil {
			return a < b
		}
		if (ea == nil) != (eb == nil) {
			return ea == nil
		}
		return keys[i] < keys[j]
	})
	return keys
}

func canonicalField(value json.RawMessage) (json.RawMessage, error) {
	var decoded any
	d := json.NewDecoder(bytes.NewReader(value))
	d.UseNumber()
	if err := d.Decode(&decoded); err != nil {
		return nil, err
	}
	return json.Marshal(decoded)
}

func changedReviewFields(before, after json.RawMessage) (map[string]ReviewFieldValue, []string, error) {
	var oldFields, newFields map[string]json.RawMessage
	if err := json.Unmarshal(before, &oldFields); err != nil {
		return nil, nil, err
	}
	if err := json.Unmarshal(after, &newFields); err != nil {
		return nil, nil, err
	}
	keys := make(map[string]bool, len(oldFields)+len(newFields))
	for key := range oldFields {
		keys[key] = true
	}
	for key := range newFields {
		keys[key] = true
	}
	changes := make(map[string]ReviewFieldValue)
	names := make([]string, 0)
	for key := range keys {
		var old, new json.RawMessage
		var had, has bool
		var err error
		if value, ok := oldFields[key]; ok {
			had = true
			old, err = canonicalField(value)
			if err != nil {
				return nil, nil, err
			}
		}
		if value, ok := newFields[key]; ok {
			has = true
			new, err = canonicalField(value)
			if err != nil {
				return nil, nil, err
			}
		}
		if had == has && bytes.Equal(old, new) {
			continue
		}
		change := ReviewFieldValue{}
		if had {
			change.Before = &old
		}
		if has {
			change.After = &new
		}
		changes[key] = change
		names = append(names, key)
	}
	sort.Strings(names)
	return changes, names, nil
}

type reviewDetailSink struct {
	writer       io.Writer
	hash         hash.Hash
	bytes, count int64
}

func newReviewDetailSink(writer io.Writer) *reviewDetailSink {
	return &reviewDetailSink{writer: writer, hash: sha256.New()}
}
func (s *reviewDetailSink) add(detail ReviewDetail) error {
	data, err := json.Marshal(detail)
	if err != nil {
		return err
	}
	data = append(data, '\n')
	n, err := s.writer.Write(data)
	if err != nil {
		return err
	}
	if n != len(data) {
		return io.ErrShortWrite
	}
	s.hash.Write(data)
	s.bytes += int64(n)
	s.count++
	return nil
}
func (s *reviewDetailSink) digest() string { return hex.EncodeToString(s.hash.Sum(nil)) }

func compareCompactRows(family, oldRoot, newRoot string, oldPacks, newPacks []Pack, before, after map[string]compactReviewedRow, sink *reviewDetailSink) (ReviewChanges, error) {
	result := ReviewChanges{ChangedFields: make(map[string]int)}
	oldCache := reviewRowCache{root: oldRoot, packs: oldPacks, index: -1}
	newCache := reviewRowCache{root: newRoot, packs: newPacks, index: -1}
	defer oldCache.close()
	defer newCache.close()
	for _, id := range sortedReviewIDs(before, after) {
		old, had := before[id]
		now, has := after[id]
		if had && has && old.hash == now.hash {
			continue
		}
		detail := ReviewDetail{Domain: family, ID: json.RawMessage(id)}
		var fields []string
		switch {
		case !had:
			result.Added++
			detail.Change = "added"
			raw, err := newCache.row(now)
			if err != nil {
				return ReviewChanges{}, err
			}
			_, detail.After, _, err = normalizedRow(raw)
			if err != nil {
				return ReviewChanges{}, err
			}
		case !has:
			result.Removed++
			detail.Change = "removed"
			raw, err := oldCache.row(old)
			if err != nil {
				return ReviewChanges{}, err
			}
			_, detail.Before, _, err = normalizedRow(raw)
			if err != nil {
				return ReviewChanges{}, err
			}
		default:
			result.Changed++
			detail.Change = "changed"
			oldRaw, err := oldCache.row(old)
			if err != nil {
				return ReviewChanges{}, err
			}
			newRaw, err := newCache.row(now)
			if err != nil {
				return ReviewChanges{}, err
			}
			detail.Fields, fields, err = changedReviewFields(oldRaw, newRaw)
			if err != nil {
				return ReviewChanges{}, err
			}
			for _, field := range fields {
				result.ChangedFields[field]++
			}
		}
		if len(result.Examples) < 5 {
			result.Examples = append(result.Examples, ReviewExample{ID: json.RawMessage(id), Change: detail.Change, Fields: fields})
		}
		if err := sink.add(detail); err != nil {
			return ReviewChanges{}, err
		}
	}
	if len(result.ChangedFields) == 0 {
		result.ChangedFields = nil
	}
	return result, nil
}

func compareTexturesDetailed(before, after map[string]Texture, sink *reviewDetailSink) (ReviewChanges, error) {
	result := ReviewChanges{}
	for _, id := range sortedReviewIDs(before, after) {
		old, had := before[id]
		now, has := after[id]
		if had && has && old.Object.SHA256 == now.Object.SHA256 {
			continue
		}
		idBytes, _ := json.Marshal(id)
		detail := ReviewDetail{Domain: "textures", ID: idBytes}
		switch {
		case !had:
			result.Added++
			detail.Change = "added"
			detail.TextureAfter = &now
		case !has:
			result.Removed++
			detail.Change = "removed"
			detail.TextureBefore = &old
		default:
			result.Changed++
			detail.Change = "changed"
			detail.TextureBefore = &old
			detail.TextureAfter = &now
		}
		if len(result.Examples) < 5 {
			result.Examples = append(result.Examples, ReviewExample{ID: idBytes, Change: detail.Change})
		}
		if err := sink.add(detail); err != nil {
			return ReviewChanges{}, err
		}
	}
	return result, nil
}
