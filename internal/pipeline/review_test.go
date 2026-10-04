package pipeline

import (
	"bytes"
	"encoding/json"
	"io"
	"os"
	"path/filepath"
	"terraria-resource-pipeline/internal/artifact"
	"terraria-resource-pipeline/internal/input"
	"testing"
)

func testPut(t *testing.T, s *artifact.Store, extension, typ string, data []byte) artifact.Object {
	t.Helper()
	obj, err := s.Put(extension, typ, func(w io.Writer) error { _, err := w.Write(data); return err })
	if err != nil {
		t.Fatal(err)
	}
	return obj
}

func testCandidate(t *testing.T, version, name string) string {
	t.Helper()
	root := filepath.Join(t.TempDir(), "candidate")
	s, err := artifact.New(root)
	if err != nil {
		t.Fatal(err)
	}
	rows := []map[string]any{{"id": 1, "name": name}}
	if version == "2" {
		rows = append(rows, map[string]any{"id": 3, "name": "new"})
	} else {
		rows = append(rows, map[string]any{"id": 2, "name": "old"})
	}
	packObj, err := s.JSON(rows)
	if err != nil {
		t.Fatal(err)
	}
	png := testPut(t, s, ".png", "image/png", []byte("png-"+version))
	textures, err := s.JSON(map[string]Texture{"Tiles/1": {Width: 1, Height: 1, Object: png}})
	if err != nil {
		t.Fatal(err)
	}
	bin := testPut(t, s, ".bin", "application/octet-stream", []byte("binary"))
	manifest := Manifest{Schema: 1, Extractor: "test", GameVersion: "1.4.5." + version, Sources: input.Sources{TextureFiles: 1},
		Families: map[string][]Pack{"items": {{Rows: len(rows), Object: packObj}}}, Textures: textures,
		RGB: RGBResources{Candidates: bin, StableCandidates: bin, SRGB: bin, TXCI: bin}, Missing: []string{}}
	data, err := json.Marshal(manifest)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(root, "manifest.json"), append(data, '\n'), 0600); err != nil {
		t.Fatal(err)
	}
	if err := Verify(root); err != nil {
		t.Fatal(err)
	}
	return root
}

func testBundledCandidate(t *testing.T) (string, Manifest, []byte) {
	t.Helper()
	root := testCandidate(t, "1", "Copper")
	_, _, pngBytes := bundleFixture(t)
	store, err := artifact.New(root)
	if err != nil {
		t.Fatal(err)
	}
	pngObj, err := store.Put(".png", "image/png", func(w io.Writer) error { _, err := w.Write(pngBytes); return err })
	if err != nil {
		t.Fatal(err)
	}
	textures := map[string]Texture{"Tiles/1": {Width: 2, Height: 1, Object: pngObj}}
	index, err := store.JSON(textures)
	if err != nil {
		t.Fatal(err)
	}
	bundles, err := BuildImageBundles(store, textures)
	if err != nil {
		t.Fatal(err)
	}
	data, err := os.ReadFile(filepath.Join(root, "manifest.json"))
	if err != nil {
		t.Fatal(err)
	}
	var manifest Manifest
	if err := json.Unmarshal(data, &manifest); err != nil {
		t.Fatal(err)
	}
	manifest.Textures = index
	manifest.ImageBundles = bundles
	data, err = json.Marshal(manifest)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(root, "manifest.json"), append(data, '\n'), 0600); err != nil {
		t.Fatal(err)
	}
	if err := Verify(root); err != nil {
		t.Fatal(err)
	}
	return root, manifest, pngBytes
}

func testReview(t *testing.T, candidate, repo string) (Review, string, string) {
	t.Helper()
	path := filepath.Join(t.TempDir(), "review.json")
	review, sha, err := WriteReview(candidate, repo, path)
	if err != nil {
		t.Fatal(err)
	}
	return review, path, sha
}

func TestReviewDetailIsDeterministicAndComplete(t *testing.T) {
	repo := testEmptySnapshot(t)
	candidate := testCandidate(t, "1", "Copper")
	store, err := artifact.New(candidate)
	if err != nil {
		t.Fatal(err)
	}
	rows := make([]map[string]any, 12)
	for i := range rows {
		rows[i] = map[string]any{"id": i + 1, "name": "item", "gameplay": map[string]any{"damage": i + 10, "nested": map[string]any{"kind": "test"}}}
	}
	object, err := store.JSON(rows)
	if err != nil {
		t.Fatal(err)
	}
	manifestPath := filepath.Join(candidate, "manifest.json")
	data, err := os.ReadFile(manifestPath)
	if err != nil {
		t.Fatal(err)
	}
	var manifest Manifest
	if err := json.Unmarshal(data, &manifest); err != nil {
		t.Fatal(err)
	}
	manifest.Families["items"] = []Pack{{Rows: len(rows), Object: object}}
	data, err = json.Marshal(manifest)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(manifestPath, data, 0600); err != nil {
		t.Fatal(err)
	}
	first, path1, sha1 := testReview(t, candidate, repo)
	second, path2, sha2 := testReview(t, candidate, repo)
	if sha1 != sha2 || first.DetailSHA256 != second.DetailSHA256 || first.DetailBytes != second.DetailBytes || first.DetailCount != 17 {
		t.Fatalf("nondeterministic or incomplete review: %+v %+v", first, second)
	}
	if len(first.Families["items"].Examples) != 5 {
		t.Fatal("summary example cap changed")
	}
	left, err := os.ReadFile(filepath.Join(filepath.Dir(path1), first.DetailFile))
	if err != nil {
		t.Fatal(err)
	}
	right, err := os.ReadFile(filepath.Join(filepath.Dir(path2), second.DetailFile))
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(left, right) {
		t.Fatal("detail lines differ for identical review")
	}
	var ids []int
	for _, line := range bytes.Split(left, []byte{'\n'}) {
		if len(line) == 0 {
			continue
		}
		var detail ReviewDetail
		if err := json.Unmarshal(line, &detail); err != nil {
			t.Fatal(err)
		}
		if detail.Domain == "items" {
			var id int
			if err := json.Unmarshal(detail.ID, &id); err != nil {
				t.Fatal(err)
			}
			ids = append(ids, id)
		}
	}
	for i, id := range ids {
		if id != i+1 {
			t.Fatalf("detail IDs not numerically sorted: %v", ids)
		}
	}
}
