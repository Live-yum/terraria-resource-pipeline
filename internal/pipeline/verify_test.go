package pipeline

import (
	"context"
	"encoding/json"
	"io"
	"io/fs"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"terraria-resource-pipeline/internal/artifact"
)

func rewriteCandidate(t *testing.T, root string, change func(*Manifest)) string {
	t.Helper()
	manifest, _, err := manifestAt(filepath.Join(root, "manifest.json"))
	if err != nil {
		t.Fatal(err)
	}
	change(&manifest)
	raw, err := json.Marshal(manifest)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(root, "manifest.json"), raw, 0600); err != nil {
		t.Fatal(err)
	}
	return digest(raw)
}

func TestVerifyRequiredPublicFamilies(t *testing.T) {
	// Independently enumerate the public contract; do not derive expected failures
	// from the implementation's required-family list.
	for _, name := range []string{
		"ids", "localization", "items", "item-index", "item-field-schema",
		"tiles", "walls", "map", "map-palette", "map-lookup", "paints", "pixel-candidates",
		"tile-sets", "wall-sets", "tile-object-data", "mount-layouts", "armor-sets",
		"prefixes", "buffs", "bestiary", "npc-frames", "dye-shaders", "player-layouts",
		"texture-references", "player-texture-bindings",
	} {
		t.Run(name, func(t *testing.T) {
			candidate := testCandidate(t, "1", "Copper")
			rewriteCandidate(t, candidate, func(m *Manifest) { delete(m.Families, name) })
			if err := Verify(candidate); err == nil || !strings.Contains(err.Error(), name) {
				t.Fatalf("missing %s accepted: %v", name, err)
			}
			// Server recomputes the release and stable-pointer hashes from the
			// rewritten manifest, so this is not a stale-hash rejection.
			server := testResourceServer(t, candidate)
			defer server.Close()
			if _, err := Snapshot(context.Background(), server.URL+"/viewer/resources/file", filepath.Join(t.TempDir(), "snapshot")); err == nil {
				t.Fatal("incomplete rehashed release accepted")
			}
			if _, err := ComputeReview(candidate, testEmptySnapshot(t)); err == nil {
				t.Fatal("incomplete candidate review accepted")
			}
		})
	}
}

func TestVerifyRehashedRelations(t *testing.T) {
	for _, mode := range []string{"empty-pack", "wrong-count", "missing-asset", "missing-binding", "missing-item", "assigned-slot", "false-sentinel"} {
		t.Run(mode, func(t *testing.T) {
			candidate := testCandidate(t, "1", "Copper")
			store, err := artifact.New(candidate)
			if err != nil {
				t.Fatal(err)
			}
			rewriteCandidate(t, candidate, func(m *Manifest) {
				switch mode {
				case "empty-pack":
					m.Families["buffs"] = nil
				case "wrong-count":
					m.Families["buffs"][0].Rows++
				default:
					family := "player-texture-bindings"
					rows := []map[string]any{{"id": "fixture", "assetId": "absent"}}
					if mode == "missing-binding" {
						delete(rows[0], "assetId")
					}
					if mode == "assigned-slot" {
						family = "mount-layouts"
						rows = []map[string]any{{"id": 1, "textureSlots": map[string]any{"front": map[string]any{"assigned": true, "assetId": nil}}}}
					}
					if mode == "false-sentinel" {
						family = "texture-references"
						rows = []map[string]any{{"id": "Wall:1", "kind": "Wall", "numericId": 1, "status": "not-drawn-sentinel", "assetId": nil}}
					}
					if mode == "missing-item" {
						family = "item-index"
						rows = []map[string]any{{"id": 1, "texture": "Tiles/1"}}
					}
					object, err := store.JSON(rows)
					if err != nil {
						t.Fatal(err)
					}
					m.Families[family] = []Pack{{Rows: len(rows), Object: object}}
				}
			})
			if err := Verify(candidate); err == nil {
				t.Fatal("rehashed invalid relation accepted")
			}
		})
	}
}

func TestPreparePublicationRejectsRequiredSubset(t *testing.T) {
	candidate := testCandidate(t, "1", "Copper")
	baseline := testEmptySnapshot(t)
	_, review, sha := testReview(t, candidate, baseline)
	rewriteCandidate(t, candidate, func(m *Manifest) { delete(m.Families, "buffs") })
	if _, err := PreparePublication(candidate, baseline, review, sha, filepath.Join(t.TempDir(), "plan.json")); err == nil || !strings.Contains(err.Error(), "buffs") {
		t.Fatalf("publication did not revalidate required coverage: %v", err)
	}
}

func TestVerifyAllowsOptionalFamiliesAndLegacyTextureScope(t *testing.T) {
	candidate := testCandidate(t, "1", "Copper")
	rewriteCandidate(t, candidate, func(m *Manifest) { m.Families["optional-future"] = m.Families["buffs"] })
	if err := Verify(candidate); err != nil {
		t.Fatal(err)
	}
}

// Exercise the public contract against an already extracted real candidate.
// This only reads resource objects; no helper or Terraria assembly is executed.
func TestRealCandidateSubsetRejected(t *testing.T) {
	candidate := os.Getenv("TRP_REAL_CANDIDATE")
	if candidate == "" {
		t.Skip("TRP_REAL_CANDIDATE not supplied")
	}
	if err := Verify(candidate); err != nil {
		t.Fatal(err)
	}
	manifest, original, err := manifestAt(filepath.Join(candidate, "manifest.json"))
	if err != nil {
		t.Fatal(err)
	}
	mutated := t.TempDir()
	// Copy public objects, never private inputs. The original
	// manifest is not changed; only the temporary copy is rewritten.
	source := filepath.Join(candidate, "objects")
	err = filepath.WalkDir(source, func(path string, entry fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		relative, err := filepath.Rel(source, path)
		if err != nil {
			return err
		}
		destination := filepath.Join(mutated, "objects", relative)
		if entry.IsDir() {
			return os.MkdirAll(destination, 0700)
		}
		input, err := os.Open(path)
		if err != nil {
			return err
		}
		defer input.Close()
		output, err := os.OpenFile(destination, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
		if err != nil {
			return err
		}
		_, copyErr := io.Copy(output, input)
		closeErr := output.Close()
		if copyErr != nil {
			return copyErr
		}
		return closeErr
	})
	if err != nil {
		t.Fatal(err)
	}
	delete(manifest.Families, "buffs")
	raw, err := json.Marshal(manifest)
	if err != nil {
		t.Fatal(err)
	}
	if digest(original) == digest(raw) {
		t.Fatal("manifest hash did not change")
	}
	if err := os.WriteFile(filepath.Join(mutated, "manifest.json"), raw, 0600); err != nil {
		t.Fatal(err)
	}
	if err := Verify(mutated); err == nil || !strings.Contains(err.Error(), "buffs") {
		t.Fatalf("real subset accepted: %v", err)
	}
	server := testResourceServer(t, mutated)
	defer server.Close()
	if _, err := Snapshot(context.Background(), server.URL+"/viewer/resources/file", filepath.Join(t.TempDir(), "snapshot")); err == nil {
		t.Fatal("real subset with recomputed release/pointer hashes accepted")
	}
	if _, err := ComputeReview(mutated, testEmptySnapshot(t)); err == nil {
		t.Fatal("real subset review accepted")
	}
}
