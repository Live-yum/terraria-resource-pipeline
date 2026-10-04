package pipeline

import (
	"encoding/json"
	"os"
	"path/filepath"
	"runtime/debug"
	"testing"

	"terraria-resource-pipeline/internal/artifact"
)

// Opt-in: official inputs remain private and are never fetched by public CI.
func TestRealCandidatePublication(t *testing.T) {
	candidate := os.Getenv("TRP_REAL_CANDIDATE")
	if candidate == "" {
		t.Skip("TRP_REAL_CANDIDATE not supplied")
	}
	previousLimit := debug.SetMemoryLimit(220000000)
	defer debug.SetMemoryLimit(previousLimit)
	monitor := newMemoryMonitor()
	defer func() {
		report := monitor.finish()
		data, err := json.Marshal(report)
		if err != nil {
			t.Fatal(err)
		}
		t.Logf("memory=%s", data)
		if output := os.Getenv("TRP_REAL_MEMORY_REPORT"); output != "" {
			if err := os.WriteFile(output, append(data, '\n'), 0600); err != nil {
				t.Fatal(err)
			}
		}
		if report.Measurement != "Go heap only on this host; full RSS acceptance requires Linux container run" && report.PeakRSSBytes > report.TargetBytes {
			t.Fatalf("publication exceeded RSS target: %d", report.PeakRSSBytes)
		}
	}()
	monitor.phase("verify-candidate")
	if raw, err := os.ReadFile(filepath.Join(candidate, ".private", "semantics", "result.json")); err == nil {
		var actual RuntimeResult
		if err := json.Unmarshal(raw, &actual); err != nil {
			t.Fatal(err)
		}
		if err := validateRuntimeCoverage(actual); err != nil {
			t.Fatal(err)
		}
	}
	if err := Verify(candidate); err != nil {
		t.Fatal(err)
	}
	manifest, _, err := manifestAt(filepath.Join(candidate, "manifest.json"))
	if err != nil {
		t.Fatal(err)
	}
	if len(manifest.Missing) > 0 || len(manifest.ImageBundles) == 0 {
		t.Fatal("real candidate is incomplete or has no image bundles")
	}
	var textures map[string]Texture
	if err := ReadObject(candidate, manifest.Textures, &textures); err != nil {
		t.Fatal(err)
	}
	var mountCount, texturelessMounts, boundMountSlots int
	for _, pack := range manifest.Families["mount-layouts"] {
		var mounts []struct {
			ID          int  `json:"id"`
			Initialized bool `json:"initialized"`
			Fields      struct {
				TotalFrames    int   `json:"totalFrames"`
				PlayerYOffsets []int `json:"playerYOffsets"`
				TextureWidth   int   `json:"textureWidth"`
				TextureHeight  int   `json:"textureHeight"`
			} `json:"fields"`
			Slots map[string]struct {
				Assigned      bool   `json:"assigned"`
				AssetID       string `json:"assetId"`
				Width, Height int
			} `json:"textureSlots"`
		}
		if err := ReadObject(candidate, pack.Object, &mounts); err != nil {
			t.Fatal(err)
		}
		for _, mount := range mounts {
			if mount.ID != mountCount || !mount.Initialized || len(mount.Slots) != 8 || mount.Fields.TotalFrames <= 0 ||
				len(mount.Fields.PlayerYOffsets) < mount.Fields.TotalFrames || mount.Fields.TextureWidth <= 0 || mount.Fields.TextureHeight <= 0 {
				t.Fatalf("incomplete official mount %d", mount.ID)
			}
			bound := 0
			for field, slot := range mount.Slots {
				if !slot.Assigned {
					if slot.AssetID != "" || slot.Width != 0 || slot.Height != 0 {
						t.Fatalf("empty mount slot has a fabricated source: %d/%s", mount.ID, field)
					}
					continue
				}
				texture, ok := textures[slot.AssetID]
				if !ok || texture.Width != slot.Width || texture.Height != slot.Height {
					t.Fatalf("mount slot is absent from the public Content catalog: %d/%s", mount.ID, field)
				}
				bound++
			}
			if bound == 0 {
				texturelessMounts++
			}
			boundMountSlots += bound
			mountCount++
		}
	}
	if mountCount != manifest.Domains["MountID"] {
		t.Fatal("mount layouts do not cover MountID.Count")
	}
	t.Logf("official mounts=%d boundSlots=%d textureless=%d", mountCount, boundMountSlots, texturelessMounts)
	var items []struct {
		ID      int    `json:"id"`
		Texture string `json:"texture"`
	}
	if err := ReadObject(candidate, manifest.Families["item-index"][0].Object, &items); err != nil {
		t.Fatal(err)
	}
	for _, item := range items {
		if _, ok := textures[item.Texture]; !ok {
			t.Fatalf("item %d refers to absent texture %q", item.ID, item.Texture)
		}
	}
	objects, err := manifestObjects(candidate, manifest)
	if err != nil {
		t.Fatal(err)
	}
	var transportBytes, imageBytes, bundleBytes, maximumBundle int64
	seen := make(map[string]bool)
	for _, object := range objects {
		transportBytes += object.Bytes
	}
	for _, object := range manifest.ImageBundles {
		bundleBytes += object.Bytes
		maximumBundle = max(maximumBundle, object.Bytes)
	}
	for _, texture := range textures {
		if !seen[texture.Object.SHA256] {
			seen[texture.Object.SHA256] = true
			imageBytes += texture.Object.Bytes
		}
	}
	t.Logf("version=%s textures=%d uniquePNG=%d bundles=%d maxBundle=%d publicObjects=%d transportBytes=%d installedBytes=%d", manifest.GameVersion, len(textures), len(seen), len(manifest.ImageBundles), maximumBundle, len(objects), transportBytes, transportBytes-bundleBytes+imageBytes)
	textures, items, objects, seen = nil, nil, nil, nil
	monitor.phase("review-initial")
	baseline := testEmptySnapshot(t)
	review, reviewFile, sha := testReview(t, candidate, baseline)
	if len(review.Missing) > 0 {
		t.Fatal("review rejected complete candidate")
	}
	monitor.phase("prepare-publication")
	plan, err := PreparePublication(candidate, baseline, reviewFile, sha, filepath.Join(t.TempDir(), "plan.json"))
	if err != nil {
		t.Fatal(err)
	}
	for _, object := range plan.Objects {
		if filepath.Ext(object.Path) == ".png" {
			t.Fatalf("raw PNG duplicated in bundled publication: %s", object.Path)
		}
	}
	monitor.phase("snapshot-baseline")
	baseline = testSnapshotCandidate(t, candidate)
	monitor.phase("review-unchanged")
	unchanged, _, _ := testReview(t, candidate, baseline)
	for name, changes := range unchanged.Families {
		if changes.Added+changes.Removed+changes.Changed != 0 {
			t.Fatalf("identical %s changed", name)
		}
	}
	if unchanged.Textures.Added+unchanged.Textures.Removed+unchanged.Textures.Changed != 0 {
		t.Fatal("identical textures changed")
	}
	for _, binary := range unchanged.Binary {
		if binary.Changed {
			t.Fatal("identical binary changed")
		}
	}
	if err := artifact.Verify(candidate, manifest.Textures); err != nil {
		t.Fatal(err)
	}
}
