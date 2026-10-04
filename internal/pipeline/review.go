package pipeline

import (
	"bufio"
	"bytes"
	"compress/gzip"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sort"
	"strings"

	"terraria-resource-pipeline/internal/artifact"
)

type ReviewExample struct {
	ID     json.RawMessage `json:"id"`
	Change string          `json:"change"`
	Fields []string        `json:"fields,omitempty"`
}

type ReviewChanges struct {
	Added         int             `json:"added"`
	Removed       int             `json:"removed"`
	Changed       int             `json:"changed"`
	ChangedFields map[string]int  `json:"changedFields,omitempty"`
	Examples      []ReviewExample `json:"examples,omitempty"`
}

type ReviewBinary struct {
	BeforeSHA256 string `json:"beforeSha256,omitempty"`
	AfterSHA256  string `json:"afterSha256"`
	Changed      bool   `json:"changed"`
}

// Review has no clock field, so identical candidate/baseline gives the
// same approval bytes and SHA-256. Incomplete candidates cannot be published.
type Review struct {
	Schema                  int                      `json:"schema"`
	CandidateManifestSHA256 string                   `json:"candidateManifestSha256"`
	BaselineManifestSHA256  string                   `json:"baselineManifestSha256,omitempty"`
	BaselineChannelSHA256   string                   `json:"baselineChannelSha256"`
	CandidateGameVersion    string                   `json:"candidateGameVersion"`
	BaselineGameVersion     string                   `json:"baselineGameVersion,omitempty"`
	Families                map[string]ReviewChanges `json:"families"`
	Textures                ReviewChanges            `json:"textures"`
	Binary                  map[string]ReviewBinary  `json:"binary"`
	Missing                 []string                 `json:"missing,omitempty"`
	DetailFile              string                   `json:"detailFile"`
	DetailSHA256            string                   `json:"detailSha256"`
	DetailBytes             int64                    `json:"detailBytes"`
	DetailCount             int64                    `json:"detailCount"`
}

type stablePointer struct {
	ManifestSHA256 string `json:"manifestSha256"`
}

func digest(data []byte) string { sum := sha256.Sum256(data); return hex.EncodeToString(sum[:]) }
func validSHA(s string) bool {
	if len(s) != 64 {
		return false
	}
	_, err := hex.DecodeString(s)
	return err == nil && s == strings.ToLower(s)
}

func readSmallRegular(path string, limit int64) ([]byte, error) {
	info, err := os.Lstat(path)
	if err != nil {
		return nil, err
	}
	if !info.Mode().IsRegular() || info.Size() > limit {
		return nil, fmt.Errorf("unsafe or oversized file: %s", path)
	}
	return os.ReadFile(path)
}

func manifestAt(path string) (Manifest, []byte, error) {
	var m Manifest
	data, err := readSmallRegular(path, 2<<20)
	if err != nil {
		return m, nil, err
	}
	if err := json.Unmarshal(data, &m); err != nil {
		return m, nil, err
	}
	if m.Schema != 1 || m.GameVersion == "" {
		return m, nil, errors.New("unsupported resource manifest")
	}
	return m, data, nil
}

func repoBaseline(repo string) (Manifest, string, error) {
	var empty Manifest
	pointerPath, err := safeRepoFile(repo, "channels/stable.json", false)
	if os.IsNotExist(err) {
		return empty, "", nil
	}
	if err != nil {
		return empty, "", err
	}
	data, err := readSmallRegular(pointerPath, 4096)
	if os.IsNotExist(err) {
		return empty, "", nil
	}
	if err != nil {
		return empty, "", err
	}
	var pointer stablePointer
	if err := json.Unmarshal(data, &pointer); err != nil {
		return empty, "", err
	}
	if !validSHA(pointer.ManifestSHA256) {
		return empty, "", errors.New("invalid stable pointer")
	}
	release, err := safeRepoFile(repo, "releases/"+pointer.ManifestSHA256+".json", false)
	if err != nil {
		return empty, "", err
	}
	m, raw, err := manifestAt(release)
	if err != nil {
		return empty, "", err
	}
	if digest(raw) != pointer.ManifestSHA256 {
		return empty, "", errors.New("stable release hash mismatch")
	}
	return m, pointer.ManifestSHA256, nil
}

func contentSHA(root string, object artifact.Object) (string, error) {
	if err := artifact.Verify(root, object); err != nil {
		return "", err
	}
	f, err := os.Open(filepath.Join(root, filepath.FromSlash(object.Path)))
	if err != nil {
		return "", err
	}
	defer f.Close()
	var r io.Reader = f
	if object.Encoding == "gzip" {
		z, e := gzip.NewReader(f)
		if e != nil {
			return "", e
		}
		defer z.Close()
		r = z
	}
	h := sha256.New()
	if _, err := io.Copy(h, r); err != nil {
		return "", err
	}
	return hex.EncodeToString(h.Sum(nil)), nil
}

// ComputeReview compares actual row IDs and uncompressed binary content.
// Object paths and compression metadata do not count as resource changes.
func ComputeReview(candidate, repo string) (Review, error) {
	return computeReview(context.Background(), candidate, repo)
}

func computeReview(ctx context.Context, candidate, repo string) (Review, error) {
	return computeReviewTo(ctx, candidate, repo, io.Discard)
}

func computeReviewTo(ctx context.Context, candidate, repo string, details io.Writer) (Review, error) {
	var review Review
	if err := Verify(candidate); err != nil {
		return review, err
	}
	current, raw, err := manifestAt(filepath.Join(candidate, "manifest.json"))
	if err != nil {
		return review, err
	}
	baseline, baselineSHA, err := repoBaseline(repo)
	if err != nil {
		return review, err
	}
	identity, err := snapshotIdentity(repo)
	if err != nil {
		return review, err
	}
	sink := newReviewDetailSink(details)
	review = Review{Schema: 2, CandidateManifestSHA256: digest(raw), BaselineManifestSHA256: baselineSHA, BaselineChannelSHA256: identity.BaselineChannelSHA256, DetailFile: "review-changes.ndjson",
		CandidateGameVersion: current.GameVersion, BaselineGameVersion: baseline.GameVersion, Families: make(map[string]ReviewChanges), Binary: make(map[string]ReviewBinary), Missing: current.Missing}
	families := make(map[string]bool)
	for name := range current.Families {
		families[name] = true
	}
	for name := range baseline.Families {
		families[name] = true
	}
	familyNames := make([]string, 0, len(families))
	for name := range families {
		familyNames = append(familyNames, name)
	}
	sort.Strings(familyNames)
	for _, name := range familyNames {
		var oldRows map[string]compactReviewedRow
		if baselineSHA != "" {
			oldRows, err = familyRows(repo, baseline.Families[name])
			if err != nil {
				return Review{}, fmt.Errorf("baseline %s: %w", name, err)
			}
		}
		newRows, err := familyRows(candidate, current.Families[name])
		if err != nil {
			return Review{}, fmt.Errorf("candidate %s: %w", name, err)
		}
		changes, err := compareCompactRows(name, repo, candidate, baseline.Families[name], current.Families[name], oldRows, newRows, sink)
		if err != nil {
			return Review{}, fmt.Errorf("review family %s: %w", name, err)
		}
		review.Families[name] = changes
	}
	var oldTextures map[string]Texture
	if baselineSHA != "" {
		if err := ReadObject(repo, baseline.Textures, &oldTextures); err != nil {
			return Review{}, err
		}
	}
	var newTextures map[string]Texture
	if err := ReadObject(candidate, current.Textures, &newTextures); err != nil {
		return Review{}, err
	}
	review.Textures, err = compareTexturesDetailed(oldTextures, newTextures, sink)
	if err != nil {
		return Review{}, err
	}
	oldObjects := map[string]artifact.Object{"rgbCandidates": baseline.RGB.Candidates, "rgbStableCandidates": baseline.RGB.StableCandidates, "srgb": baseline.RGB.SRGB, "txci": baseline.RGB.TXCI}
	newObjects := map[string]artifact.Object{"rgbCandidates": current.RGB.Candidates, "rgbStableCandidates": current.RGB.StableCandidates, "srgb": current.RGB.SRGB, "txci": current.RGB.TXCI}
	for name, object := range newObjects {
		after, err := contentSHA(candidate, object)
		if err != nil {
			return Review{}, err
		}
		before := ""
		if baselineSHA != "" {
			before, err = contentSHA(repo, oldObjects[name])
			if err != nil {
				return Review{}, err
			}
		}
		review.Binary[name] = ReviewBinary{BeforeSHA256: before, AfterSHA256: after, Changed: before != after}
	}
	binaryNames := make([]string, 0, len(review.Binary))
	for name := range review.Binary {
		binaryNames = append(binaryNames, name)
	}
	sort.Strings(binaryNames)
	for _, name := range binaryNames {
		value := review.Binary[name]
		if !value.Changed {
			continue
		}
		id, _ := json.Marshal(name)
		if err := sink.add(ReviewDetail{Domain: "binary", ID: id, Change: "changed", BeforeSHA256: value.BeforeSHA256, AfterSHA256: value.AfterSHA256}); err != nil {
			return Review{}, err
		}
	}
	review.DetailSHA256 = sink.digest()
	review.DetailBytes = sink.bytes
	review.DetailCount = sink.count
	return review, nil
}

// WriteReview creates an immutable approval file and returns its SHA-256.
func WriteReview(candidate, repo, path string) (Review, string, error) {
	if filepath.Base(path) == "review-changes.ndjson" {
		return Review{}, "", errors.New("review path conflicts with detail file")
	}
	if _, err := os.Lstat(path); err == nil {
		return Review{}, "", os.ErrExist
	} else if !os.IsNotExist(err) {
		return Review{}, "", err
	}
	detailPath := filepath.Join(filepath.Dir(path), "review-changes.ndjson")
	detailFile, err := os.OpenFile(detailPath, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
	if err != nil {
		return Review{}, "", err
	}
	buffer := bufio.NewWriterSize(detailFile, 64<<10)
	review, err := computeReviewTo(context.Background(), candidate, repo, buffer)
	if err != nil {
		detailFile.Close()
		os.Remove(detailPath)
		return Review{}, "", err
	}
	if err := buffer.Flush(); err != nil {
		detailFile.Close()
		os.Remove(detailPath)
		return Review{}, "", err
	}
	if err := detailFile.Sync(); err != nil {
		detailFile.Close()
		os.Remove(detailPath)
		return Review{}, "", err
	}
	if err := detailFile.Close(); err != nil {
		os.Remove(detailPath)
		return Review{}, "", err
	}
	data, err := json.MarshalIndent(review, "", "  ")
	if err != nil {
		os.Remove(detailPath)
		return Review{}, "", err
	}
	data = append(data, '\n')
	f, err := os.OpenFile(path, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
	if err != nil {
		os.Remove(detailPath)
		return Review{}, "", err
	}
	_, err = f.Write(data)
	closeErr := f.Close()
	if err != nil {
		os.Remove(detailPath)
		os.Remove(path)
		return Review{}, "", err
	}
	if closeErr != nil {
		os.Remove(detailPath)
		os.Remove(path)
		return Review{}, "", closeErr
	}
	return review, digest(data), nil
}

func verifyReviewDetail(reviewPath string, review Review) error {
	if review.DetailFile != "review-changes.ndjson" || !validSHA(review.DetailSHA256) || review.DetailBytes < 0 || review.DetailCount < 0 {
		return errors.New("invalid review detail descriptor")
	}
	path := filepath.Join(filepath.Dir(reviewPath), review.DetailFile)
	info, err := os.Lstat(path)
	if err != nil {
		return err
	}
	if !info.Mode().IsRegular() || info.Size() != review.DetailBytes {
		return errors.New("review detail size or file type mismatch")
	}
	file, err := os.Open(path)
	if err != nil {
		return err
	}
	defer file.Close()
	hash := sha256.New()
	buf := make([]byte, 64<<10)
	var count, total int64
	var last byte
	for {
		n, readErr := file.Read(buf)
		if n > 0 {
			hash.Write(buf[:n])
			count += int64(bytes.Count(buf[:n], []byte{'\n'}))
			total += int64(n)
			last = buf[n-1]
		}
		if readErr == io.EOF {
			break
		}
		if readErr != nil {
			return readErr
		}
	}
	if total != review.DetailBytes || count != review.DetailCount || (total > 0 && last != '\n') || hex.EncodeToString(hash.Sum(nil)) != review.DetailSHA256 {
		return errors.New("review detail digest or row count mismatch")
	}
	return nil
}

func safeRepoFile(repo, relative string, create bool) (string, error) {
	if filepath.IsAbs(relative) || relative == "" || strings.Contains(relative, "\\") {
		return "", errors.New("invalid repository path")
	}
	parts := strings.Split(relative, "/")
	current := repo
	for i, part := range parts {
		if part == "" || part == "." || part == ".." {
			return "", errors.New("invalid repository path")
		}
		current = filepath.Join(current, part)
		info, err := os.Lstat(current)
		if os.IsNotExist(err) {
			if i < len(parts)-1 && create {
				if err := os.Mkdir(current, 0700); err != nil {
					return "", err
				}
				continue
			}
			if i == len(parts)-1 {
				return current, nil
			}
			return "", err
		}
		if err != nil {
			return "", err
		}
		if info.Mode()&os.ModeSymlink != 0 {
			return "", fmt.Errorf("repository path contains symlink: %s", relative)
		}
		if i < len(parts)-1 && !info.IsDir() {
			return "", fmt.Errorf("repository path is not a directory: %s", relative)
		}
		if i == len(parts)-1 && !info.Mode().IsRegular() {
			return "", fmt.Errorf("repository target is not a regular file: %s", relative)
		}
	}
	return current, nil
}

func manifestObjects(root string, m Manifest) ([]artifact.Object, error) {
	all := []artifact.Object{m.Textures, m.RGB.Candidates, m.RGB.StableCandidates, m.RGB.SRGB, m.RGB.TXCI}
	for _, packs := range m.Families {
		for _, pack := range packs {
			all = append(all, pack.Object)
		}
	}
	var textures map[string]Texture
	if err := ReadObject(root, m.Textures, &textures); err != nil {
		return nil, err
	}
	if len(m.ImageBundles) > 0 {
		for _, bundle := range m.ImageBundles {
			all = append(all, bundle)
		}
	} else {
		for _, texture := range textures {
			all = append(all, texture.Object)
		}
	}
	unique := make(map[string]artifact.Object)
	for _, obj := range all {
		if prev, ok := unique[obj.Path]; ok && prev != obj {
			return nil, errors.New("conflicting object descriptors")
		}
		unique[obj.Path] = obj
	}
	paths := make([]string, 0, len(unique))
	for path := range unique {
		paths = append(paths, path)
	}
	sort.Strings(paths)
	result := make([]artifact.Object, 0, len(paths))
	for _, path := range paths {
		result = append(result, unique[path])
	}
	return result, nil
}
