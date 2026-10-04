package pipeline

import (
	"encoding/json"
	"fmt"
)

// Revalidate the published projection independently of the private runtime
// report. Content hashes alone cannot detect a consistently rewritten subset.
func verifyPublicCoverage(root string, manifest Manifest, textures map[string]Texture) error {
	for _, name := range requiredPublicFamilies() {
		if len(manifest.Families[name]) == 0 {
			return fmt.Errorf("required public resource family missing or empty: %s", name)
		}
	}
	identities := make(map[string]map[string]bool)
	for name, packs := range manifest.Families {
		seen := make(map[string]bool)
		for _, pack := range packs {
			var rows []json.RawMessage
			if err := ReadObject(root, pack.Object, &rows); err != nil {
				return fmt.Errorf("%s: %w", name, err)
			}
			if pack.Rows <= 0 || len(rows) != pack.Rows {
				return fmt.Errorf("%s: public pack row count mismatch", name)
			}
			for _, raw := range rows {
				identity, _, row, err := decodeRow(raw)
				if err != nil {
					return fmt.Errorf("%s: %w", name, err)
				}
				if seen[identity] {
					return fmt.Errorf("%s: duplicate public metadata id %s", name, identity)
				}
				seen[identity] = true
				switch name {
				case "texture-references", "player-texture-bindings", "mount-layouts", "item-index":
					if err := verifyTextureReferences(row, textures); err != nil {
						return fmt.Errorf("%s/%s: %w", name, identity, err)
					}
				}
				if name == "texture-references" || name == "player-texture-bindings" {
					asset, ok := row["assetId"].(string)
					sentinel := name == "texture-references" && identity == `"Wall:0"` && row["kind"] == "Wall" &&
						row["numericId"] == json.Number("0") && row["status"] == "not-drawn-sentinel" && row["assetId"] == nil
					if (!ok || asset == "") && !sentinel {
						return fmt.Errorf("%s/%s: missing texture binding", name, identity)
					}
				}
				if name == "item-index" {
					asset, ok := row["texture"].(string)
					if _, exists := textures[asset]; !ok || !exists {
						return fmt.Errorf("item-index/%s: texture absent from catalog: %q", identity, asset)
					}
				}
			}
		}
		if name == "items" || name == "item-index" {
			identities[name] = seen
		}
	}
	if len(identities["items"]) != len(identities["item-index"]) {
		return fmt.Errorf("items and item-index identity coverage mismatch")
	}
	for id := range identities["items"] {
		if !identities["item-index"][id] {
			return fmt.Errorf("item-index missing item identity: %s", id)
		}
	}
	return nil
}

// Published assetId fields name concrete Content keys. Null slots describe
// unassigned optional textures and do not require an invented catalog entry.
func verifyTextureReferences(value any, textures map[string]Texture) error {
	switch value := value.(type) {
	case map[string]any:
		if value["assigned"] == true {
			if asset, ok := value["assetId"].(string); !ok || asset == "" {
				return fmt.Errorf("assigned texture slot lacks asset identity")
			}
		}
		for key, child := range value {
			if key == "assetId" && child != nil {
				asset, ok := child.(string)
				if _, exists := textures[asset]; !ok || !exists {
					return fmt.Errorf("texture reference absent from catalog: %v", child)
				}
			}
			if err := verifyTextureReferences(child, textures); err != nil {
				return err
			}
		}
	case []any:
		for _, child := range value {
			if err := verifyTextureReferences(child, textures); err != nil {
				return err
			}
		}
	}
	return nil
}
