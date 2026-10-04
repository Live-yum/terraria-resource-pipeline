package pipeline

import (
	"bufio"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"unicode/utf16"
)

const textureScopeMode = "consumer-closure"
const maxPrivateTextureCatalogBytes = 64 << 20

func publicTextureClosureAvailable(capabilities map[string]any) bool {
	closure, ok := capabilities["publicTextureClosure"].(map[string]any)
	return ok && closure["available"] == true
}

func readPrivateTextureCatalog(path string, sourceCount int) (map[string]Texture, error) {
	info, err := os.Lstat(path)
	if err != nil || !info.Mode().IsRegular() || info.Size() > maxPrivateTextureCatalogBytes {
		return nil, errors.New("private texture catalog missing or exceeds budget")
	}
	file, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer file.Close()
	decoder := json.NewDecoder(file)
	var catalog map[string]Texture
	if err := decoder.Decode(&catalog); err != nil {
		return nil, err
	}
	var trailing any
	if err := decoder.Decode(&trailing); err != io.EOF {
		return nil, errors.New("private texture catalog has trailing data")
	}
	if len(catalog) != sourceCount {
		return nil, errors.New("private texture catalog source count mismatch")
	}
	return catalog, nil
}

// .NET's StringComparer.Ordinal compares UTF-16 code units, rather than Go's
// UTF-8 string ordering. The helper promises this order for its NDJSON IDs.
func ordinalLess(a, b string) bool {
	aUnits, bUnits := utf16.Encode([]rune(a)), utf16.Encode([]rune(b))
	for i := 0; i < len(aUnits) && i < len(bUnits); i++ {
		if aUnits[i] != bUnits[i] {
			return aUnits[i] < bUnits[i]
		}
	}
	return len(aUnits) < len(bUnits)
}

func selectPublicTextures(root string, descriptor RuntimeFamily, catalog map[string]Texture) (map[string]Texture, error) {
	path, err := validatedRuntimeFile(root, descriptor, 16<<20, "publicTextures")
	if err != nil {
		return nil, err
	}
	if descriptor.Count <= 0 || descriptor.Count > len(catalog) {
		return nil, errors.New("public texture count exceeds source catalog")
	}
	file, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer file.Close()
	selected := make(map[string]Texture, descriptor.Count)
	scanner := bufio.NewScanner(file)
	scanner.Buffer(make([]byte, 4096), 64<<10)
	var previous string
	for scanner.Scan() {
		if len(selected) >= descriptor.Count {
			return nil, errors.New("public texture selection exceeds declared row count")
		}
		var key string
		if err := json.Unmarshal(scanner.Bytes(), &key); err != nil || key == "" {
			return nil, fmt.Errorf("invalid public texture key at row %d", len(selected)+1)
		}
		if len(selected) > 0 && !ordinalLess(previous, key) {
			return nil, fmt.Errorf("public texture keys duplicate or out of order at row %d", len(selected)+1)
		}
		texture, ok := catalog[key]
		if !ok {
			return nil, fmt.Errorf("public texture key absent from source catalog: %q", key)
		}
		selected[key] = texture
		previous = key
	}
	if err := scanner.Err(); err != nil {
		return nil, err
	}
	if len(selected) != descriptor.Count {
		return nil, errors.New("public texture selection row count mismatch")
	}
	return selected, nil
}

func validateTextureScope(manifest Manifest, actual int) error {
	if manifest.TextureScope == nil {
		if actual != manifest.Sources.TextureFiles {
			return errors.New("manifest texture count mismatch")
		}
		return nil
	}
	scope := manifest.TextureScope
	if scope.Mode != textureScopeMode || scope.SourceCount <= 0 || scope.SourceCount != manifest.Sources.TextureFiles ||
		scope.SelectedCount <= 0 || scope.SelectedCount > scope.SourceCount || scope.SelectedCount != actual ||
		!publicTextureClosureAvailable(manifest.Capabilities) || len(manifest.Families["player-texture-bindings"]) == 0 {
		return errors.New("manifest texture closure contract mismatch")
	}
	return nil
}
