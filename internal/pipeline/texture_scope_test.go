package pipeline

import (
	"crypto/sha256"
	"encoding/hex"
	"os"
	"path/filepath"
	"testing"

	"terraria-resource-pipeline/internal/input"
)

func TestPublicTextureSelection(t *testing.T) {
	root := t.TempDir()
	path := filepath.Join(root, "ids.ndjson")
	catalog := map[string]Texture{"A": {}, "B": {}, "𐀀": {}, "\ue000": {}}
	for _, test := range []struct {
		name, rows string
		count      int
		valid      bool
	}{
		{"valid", "\"A\"\n\"B\"\n", 2, true},
		{"utf16 ordinal", "\"𐀀\"\n\"\ue000\"\n", 2, true},
		{"duplicate", "\"A\"\n\"A\"\n", 2, false},
		{"unsorted", "\"B\"\n\"A\"\n", 2, false},
		{"missing key", "\"C\"\n", 1, false},
		{"short", "\"A\"\n", 2, false},
		{"extra", "\"A\"\n\"B\"\n", 1, false},
		{"nonstring", "{\"key\":\"A\"}\n", 1, false},
		{"trailing json", "\"A\" true\n", 1, false},
		{"blank", "\"A\"\n\n", 2, false},
	} {
		t.Run(test.name, func(t *testing.T) {
			data := []byte(test.rows)
			if err := os.WriteFile(path, data, 0600); err != nil {
				t.Fatal(err)
			}
			hash := sha256.Sum256(data)
			selected, err := selectPublicTextures(root, RuntimeFamily{Path: "ids.ndjson", Count: test.count, SHA256: hex.EncodeToString(hash[:])}, catalog)
			if (err == nil) != test.valid || (err == nil && len(selected) != test.count) {
				t.Fatalf("selected=%d err=%v", len(selected), err)
			}
		})
	}
}

func TestPublicTextureDescriptorSafety(t *testing.T) {
	root := t.TempDir()
	path := filepath.Join(root, "ids.ndjson")
	data := []byte("\"A\"\n")
	if err := os.WriteFile(path, data, 0600); err != nil {
		t.Fatal(err)
	}
	hash := sha256.Sum256(data)
	descriptor := RuntimeFamily{Path: "ids.ndjson", Count: 1, SHA256: hex.EncodeToString(hash[:])}
	for _, bad := range []RuntimeFamily{
		{Path: "../ids.ndjson", Count: 1, SHA256: descriptor.SHA256},
		{Path: "ids.ndjson", Count: 1, SHA256: hex.EncodeToString(make([]byte, 32))},
	} {
		if _, err := selectPublicTextures(root, bad, map[string]Texture{"A": {}}); err == nil {
			t.Fatalf("accepted unsafe descriptor %+v", bad)
		}
	}
	link := filepath.Join(root, "link.ndjson")
	if err := os.Symlink(path, link); err == nil {
		descriptor.Path = "link.ndjson"
		if _, err := selectPublicTextures(root, descriptor, map[string]Texture{"A": {}}); err == nil {
			t.Fatal("accepted symlink")
		}
	}
}

func TestTextureScopeManifestContract(t *testing.T) {
	manifest := Manifest{Sources: input.Sources{TextureFiles: 3}}
	if err := validateTextureScope(manifest, 3); err != nil {
		t.Fatal(err)
	}
	if err := validateTextureScope(manifest, 2); err == nil {
		t.Fatal("legacy manifest silently omitted a source texture")
	}
	manifest.TextureScope = &TextureScope{Mode: textureScopeMode, SourceCount: 3, SelectedCount: 2}
	manifest.Capabilities = map[string]any{"publicTextureClosure": map[string]any{"available": true}}
	manifest.Families = map[string][]Pack{"player-texture-bindings": {{Rows: 1}}}
	if err := validateTextureScope(manifest, 2); err != nil {
		t.Fatal(err)
	}
	for _, change := range []func(*Manifest){
		func(m *Manifest) { m.TextureScope.Mode = "all-content" },
		func(m *Manifest) { m.TextureScope.SourceCount = 2 },
		func(m *Manifest) { m.TextureScope.SelectedCount = 3 },
		func(m *Manifest) { m.Capabilities = nil },
		func(m *Manifest) { m.Families = nil },
	} {
		copy := manifest
		scope := *manifest.TextureScope
		copy.TextureScope = &scope
		change(&copy)
		if err := validateTextureScope(copy, 2); err == nil {
			t.Fatalf("accepted invalid scope %+v", copy.TextureScope)
		}
	}
}

func TestPrivateTextureCatalogRejectsTrailingData(t *testing.T) {
	path := filepath.Join(t.TempDir(), "catalog.json")
	if err := os.WriteFile(path, []byte("{\"A\":{}}\n{}"), 0600); err != nil {
		t.Fatal(err)
	}
	if _, err := readPrivateTextureCatalog(path, 1); err == nil {
		t.Fatal("accepted trailing private catalog data")
	}
}

func TestRuntimeRequiresPublicTextureClosure(t *testing.T) {
	families := make(map[string]RuntimeFamily)
	for _, name := range []string{
		"ids", "localization", "items", "item-field-schema", "item-tooltips", "item-ui-tooltips", "research",
		"tiles", "walls", "map", "map-palette", "map-lookup", "paints", "pixel-candidates",
		"tile-sets", "wall-sets", "tile-object-data", "mount-layouts", "armor-sets",
		"prefixes", "buffs", "bestiary", "npc-frames", "dye-shaders", "player-layouts",
		"texture-references", "player-texture-bindings", "player-draw-plans",
	} {
		families[name] = RuntimeFamily{Count: 1}
	}
	result := RuntimeResult{Families: families, PublicTextures: RuntimeFamily{Path: "ids.ndjson", Count: 1},
		Domains: map[string]int{"MountID": 1},
		Capabilities: map[string]any{"publicTextureClosure": map[string]any{"available": true},
			"mountLayouts": map[string]any{"available": true, "textureDimensionsBound": true, "entityCount": 1, "populated": 1}}}
	if err := validateRuntimeCoverage(result); err != nil {
		t.Fatal(err)
	}
	bound := result.Capabilities["mountLayouts"]
	for _, invalid := range []any{nil,
		map[string]any{"available": true, "textureDimensionsBound": false, "entityCount": 1, "populated": 1},
		map[string]any{"available": true, "textureDimensionsBound": true, "entityCount": 1, "populated": 0},
		map[string]any{"available": true, "textureDimensionsBound": true, "entityCount": 2, "populated": 2}} {
		result.Capabilities["mountLayouts"] = invalid
		if err := validateRuntimeCoverage(result); err == nil {
			t.Fatal("accepted unbound or incomplete official mount textures")
		}
	}
	result.Capabilities["mountLayouts"] = bound
	delete(families, "player-texture-bindings")
	if err := validateRuntimeCoverage(result); err == nil {
		t.Fatal("accepted missing player texture bindings")
	}
	families["player-texture-bindings"] = RuntimeFamily{Count: 1}
	result.Capabilities = nil
	if err := validateRuntimeCoverage(result); err == nil {
		t.Fatal("accepted unavailable public texture closure")
	}
}
