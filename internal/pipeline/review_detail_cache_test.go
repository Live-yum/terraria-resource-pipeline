package pipeline

import (
	"bytes"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"os"
	"path/filepath"
	"testing"

	"terraria-resource-pipeline/internal/artifact"
)

func TestReviewRowsOutOfPackOrder(t *testing.T) {
	root := t.TempDir()
	store, err := artifact.New(root)
	if err != nil {
		t.Fatal(err)
	}
	var packs []Pack
	for _, rows := range [][]map[string]any{
		{{"id": 3, "name": "three"}, {"id": 1, "name": "one"}},
		{{"id": 4, "name": "four"}, {"id": 2, "name": "two"}},
	} {
		obj, err := store.JSON(rows)
		if err != nil {
			t.Fatal(err)
		}
		packs = append(packs, Pack{Rows: len(rows), Object: obj})
	}
	refs, err := familyRows(root, packs)
	if err != nil {
		t.Fatal(err)
	}
	var output bytes.Buffer
	sink := newReviewDetailSink(&output)
	changes, err := compareCompactRows("items", "", root, nil, packs, nil, refs, sink)
	if err != nil {
		t.Fatal(err)
	}
	if changes.Added != 4 || changes.Removed != 0 || changes.Changed != 0 {
		t.Fatalf("changes: %+v", changes)
	}
	var want bytes.Buffer
	for _, pair := range []struct {
		id   int
		name string
	}{{1, "one"}, {2, "two"}, {3, "three"}, {4, "four"}} {
		row, err := json.Marshal(map[string]any{"id": pair.id, "name": pair.name})
		if err != nil {
			t.Fatal(err)
		}
		line, err := json.Marshal(ReviewDetail{Domain: "items", ID: json.RawMessage{byte('0' + pair.id)}, Change: "added", After: row})
		if err != nil {
			t.Fatal(err)
		}
		want.Write(line)
		want.WriteByte('\n')
	}
	if !bytes.Equal(output.Bytes(), want.Bytes()) {
		t.Fatalf("detail bytes differ:\n%s\nwant:\n%s", output.Bytes(), want.Bytes())
	}
	sum := sha256.Sum256(want.Bytes())
	if sink.digest() != hex.EncodeToString(sum[:]) || sink.bytes != int64(want.Len()) || sink.count != 4 {
		t.Fatal("detail digest or count changed")
	}
	cache := reviewRowCache{root: root, packs: packs, index: -1}
	defer cache.close()
	for _, id := range []string{"1", "2", "3", "4", "1"} {
		if _, err := cache.row(refs[id]); err != nil {
			t.Fatal(err)
		}
		if cache.bytes > reviewPackCacheBytes {
			t.Fatalf("cache exceeded bound: %d", cache.bytes)
		}
	}
	if len(cache.stored) != 2 {
		t.Fatalf("expected both packs retained, got %d", len(cache.stored))
	}
	if err := os.WriteFile(filepath.Join(root, filepath.FromSlash(packs[0].Object.Path)), []byte("tampered"), 0600); err != nil {
		t.Fatal(err)
	}
	fresh := reviewRowCache{root: root, packs: packs, index: -1}
	defer fresh.close()
	if _, err := fresh.row(refs["1"]); err == nil {
		t.Fatal("pack hash mismatch was not detected")
	}
}

func TestReviewPackCacheFallbackAndCount(t *testing.T) {
	root := t.TempDir()
	store, err := artifact.New(root)
	if err != nil {
		t.Fatal(err)
	}
	noise := make([]byte, 1100000)
	if _, err := rand.Read(noise); err != nil {
		t.Fatal(err)
	}
	obj, err := store.JSON([]map[string]any{{"id": 7, "data": hex.EncodeToString(noise)}})
	if err != nil {
		t.Fatal(err)
	}
	if obj.Bytes <= 1<<20 {
		t.Fatalf("fixture did not exceed cache eligibility: %d", obj.Bytes)
	}
	cache := reviewRowCache{root: root, packs: []Pack{{Rows: 1, Object: obj}}, index: -1}
	defer cache.close()
	row, err := cache.row(compactReviewedRow{pack: 0, row: 0})
	if err != nil {
		t.Fatal(err)
	}
	if len(row) == 0 || len(cache.stored) != 0 {
		t.Fatal("large pack fallback lost row or cached oversized pack")
	}
	small, err := store.JSON([]map[string]any{{"id": 1}})
	if err != nil {
		t.Fatal(err)
	}
	badCount := reviewRowCache{root: root, packs: []Pack{{Rows: 2, Object: small}}, index: -1}
	defer badCount.close()
	if _, err := badCount.row(compactReviewedRow{pack: 0, row: 0}); err == nil {
		t.Fatal("manifest row count mismatch was not detected")
	}
}
