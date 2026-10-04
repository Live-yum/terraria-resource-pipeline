package pipeline

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"image"
	"image/png"
	"io"
	"io/fs"
	"os"
	"path/filepath"
	"runtime/debug"
	"sort"
	"strings"

	"terraria-resource-pipeline/internal/artifact"
	"terraria-resource-pipeline/internal/derived"
	"terraria-resource-pipeline/internal/input"
	"terraria-resource-pipeline/internal/xnb"
)

const Version = "1.1.0"

type Pack struct {
	Key    string          `json:"key,omitempty"`
	First  int             `json:"first,omitempty"`
	Last   int             `json:"last,omitempty"`
	Rows   int             `json:"rows"`
	Object artifact.Object `json:"object"`
}
type Texture struct {
	Width  int             `json:"width"`
	Height int             `json:"height"`
	Object artifact.Object `json:"object"`
}
type RGBResources struct {
	Candidates       artifact.Object `json:"candidates"`
	StableCandidates artifact.Object `json:"stableCandidates"`
	SRGB             artifact.Object `json:"srgb"`
	TXCI             artifact.Object `json:"txci"`
}
type TextureScope struct {
	Mode          string `json:"mode"`
	SourceCount   int    `json:"sourceCount"`
	SelectedCount int    `json:"selectedCount"`
}
type Manifest struct {
	Schema       int                        `json:"schema"`
	Extractor    string                     `json:"extractor"`
	GameVersion  string                     `json:"gameVersion"`
	Sources      input.Sources              `json:"sources"`
	Domains      map[string]int             `json:"domains"`
	MapLayout    map[string]int             `json:"mapLayout"`
	Capabilities map[string]any             `json:"capabilities"`
	Families     map[string][]Pack          `json:"families"`
	Textures     artifact.Object            `json:"textures"`
	TextureScope *TextureScope              `json:"textureScope,omitempty"`
	ImageBundles map[string]artifact.Object `json:"imageBundles,omitempty"`
	RGB          RGBResources               `json:"rgb"`
	Missing      []string                   `json:"missing"`
}
type Report struct {
	Status         string          `json:"status"`
	Error          string          `json:"error,omitempty"`
	ManifestSHA256 string          `json:"manifestSha256,omitempty"`
	GameVersion    string          `json:"gameVersion,omitempty"`
	Archive        input.Inventory `json:"archive"`
	RuntimeMetrics map[string]any  `json:"runtimeMetrics,omitempty"`
	Memory         MemoryReport    `json:"memory"`
	Counts         map[string]int  `json:"counts"`
	Missing        []string        `json:"missing"`
}
type Options struct {
	Input, Output, RuntimeImage string
	Progress                    func(string)
}

func writeJSON(filename string, value any) error {
	file, err := os.OpenFile(filename, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
	if err != nil {
		return err
	}
	err = json.NewEncoder(file).Encode(value)
	closeErr := file.Close()
	if err != nil {
		return err
	}
	return closeErr
}

// Extract runs each memory-heavy stage sequentially. Runtime, archive and
// diagnostic files remain private and are never copied to the CDN repository.
func Extract(ctx context.Context, options Options) (report Report, err error) {
	output, err := filepath.Abs(options.Output)
	if err != nil {
		return report, err
	}
	if err = os.Mkdir(output, 0700); err != nil {
		return report, fmt.Errorf("output must be a new attempt directory: %w", err)
	}
	private := filepath.Join(output, ".private")
	if err = os.Mkdir(private, 0700); err != nil {
		return report, err
	}
	report.Status, report.Counts = "failed", make(map[string]int)
	monitor := newMemoryMonitor()
	previousLimit := debug.SetMemoryLimit(220000000)
	defer debug.SetMemoryLimit(previousLimit)
	defer func() {
		report.Memory = monitor.finish()
		if err != nil {
			report.Error = err.Error()
		}
		if saveErr := writeJSON(filepath.Join(private, "report.json"), report); saveErr != nil && err == nil {
			err = saveErr
		}
	}()
	phase := func(name string) {
		debug.FreeOSMemory()
		monitor.phase(name)
		if options.Progress != nil {
			options.Progress(name)
		}
	}
	phase("unpack")
	root := filepath.Join(private, "input")
	report.Archive, err = input.ExtractZIP(ctx, options.Input, root, input.DefaultLimits())
	if err != nil {
		return report, err
	}
	phase("identify-input")
	sources, err := input.Discover(root)
	if err != nil {
		return report, err
	}
	store, err := artifact.New(output)
	if err != nil {
		return report, err
	}
	phase("textures")
	textures, err := exportTextures(ctx, store, sources.Content)
	if err != nil {
		return report, err
	}
	if len(textures) != sources.TextureFiles {
		return report, errors.New("texture inventory coverage mismatch")
	}
	fullCatalog := filepath.Join(private, "texture-catalog.json")
	if err := writeJSON(fullCatalog, textures); err != nil {
		return report, err
	}
	dimensions := filepath.Join(private, "texture-dimensions.ndjson")
	if err := writeDimensions(dimensions, textures); err != nil {
		return report, err
	}
	textures = nil
	phase("game-metadata")
	semantics := filepath.Join(private, "semantics")
	runtimeResult, err := RunRuntime(ctx, options.RuntimeImage, root, sources.Server, semantics, dimensions)
	if err != nil {
		return report, err
	}
	report.GameVersion, report.RuntimeMetrics, report.Missing = runtimeResult.GameVersion, runtimeResult.Metrics, runtimeResult.Missing
	if runtimeResult.AssemblySHA256 != sources.ServerSHA256 {
		return report, errors.New("game assembly changed after source inventory")
	}
	if len(runtimeResult.Errors) != 0 {
		return report, fmt.Errorf("game extraction errors: %v", runtimeResult.Errors)
	}
	if err := validateRuntimeCoverage(runtimeResult); err != nil {
		return report, err
	}
	if _, ok := runtimeResult.Families["texture-references"]; !ok {
		return report, errors.New("game did not provide actual texture bindings")
	}
	phase("texture-scope")
	textures, err = readPrivateTextureCatalog(fullCatalog, sources.TextureFiles)
	if err != nil {
		return report, err
	}
	textures, err = selectPublicTextures(semantics, runtimeResult.PublicTextures, textures)
	if err != nil {
		return report, err
	}
	report.Counts["textures"] = len(textures)
	textureIndex, err := store.JSON(textures)
	if err != nil {
		return report, err
	}
	phase("texture-packs")
	imageBundles, err := BuildImageBundles(store, textures)
	if err != nil {
		return report, err
	}
	report.Counts["image-bundles"] = len(imageBundles)
	textures = nil
	manifest := Manifest{Schema: 1, Extractor: Version, GameVersion: runtimeResult.GameVersion, Sources: sources,
		Domains: runtimeResult.Domains, MapLayout: runtimeResult.MapLayout, Capabilities: runtimeResult.Capabilities, Families: make(map[string][]Pack), Missing: runtimeResult.Missing, Textures: textureIndex, ImageBundles: imageBundles,
		TextureScope: &TextureScope{Mode: textureScopeMode, SourceCount: sources.TextureFiles, SelectedCount: report.Counts["textures"]}}
	phase("metadata-packs")
	names := make([]string, 0, len(runtimeResult.Families))
	for name := range runtimeResult.Families {
		names = append(names, name)
	}
	sort.Strings(names)
	for _, name := range names {
		if err := ctx.Err(); err != nil {
			return report, err
		}
		family := runtimeResult.Families[name]
		// Official finite draw outputs validate the local compositor. They are
		// private test oracles, not inputs that every offline client must install.
		if name == "player-draw-plans" {
			report.Counts[name] = family.Count
			continue
		}
		if name != "items" && name != "research" && name != "item-tooltips" && name != "item-ui-tooltips" {
			packs, opPacks, err := packFamily(store, filepath.Join(semantics, filepath.FromSlash(family.Path)), family.Count, name == "player-draw-plans")
			if err != nil {
				return report, fmt.Errorf("%s: %w", name, err)
			}
			manifest.Families[name] = packs
			if len(opPacks) > 0 {
				manifest.Families["player-draw-operations"] = opPacks
			}
			report.Counts[name] = family.Count
			debug.FreeOSMemory()
			continue
		}
		rows, err := readRows(filepath.Join(semantics, filepath.FromSlash(family.Path)), family.Count)
		if err != nil {
			return report, fmt.Errorf("%s: %w", name, err)
		}
		report.Counts[name] = len(rows)
		// Research and tooltips are merged into item details, rather than shipped
		// as a second independent copy of each item's data.
		if name == "research" || name == "item-tooltips" || name == "item-ui-tooltips" {
			continue
		}
		if name == "items" {
			packs, index, err := exportItems(store, rows, semantics, runtimeResult.Families["research"], runtimeResult.Families["item-tooltips"], runtimeResult.Families["item-ui-tooltips"], runtimeResult.Families["texture-references"])
			if err != nil {
				return report, err
			}
			manifest.Families["items"] = packs
			manifest.Families["item-index"] = []Pack{index}
		} else {
			object, err := store.JSON(rows)
			if err != nil {
				return report, err
			}
			manifest.Families[name] = []Pack{{Rows: len(rows), Object: object}}
		}
		rows = nil
		debug.FreeOSMemory()
	}
	phase("rgb-candidates")
	family, ok := runtimeResult.Families["pixel-candidates"]
	if !ok {
		return report, errors.New("game did not provide painted map colors")
	}
	candidates, err := readCandidates(filepath.Join(semantics, filepath.FromSlash(family.Path)), family.Count)
	if err != nil {
		return report, err
	}
	manifest.RGB.Candidates, err = store.JSON(candidateRows(candidates))
	if err != nil {
		return report, err
	}
	stable := derived.StableCandidates(candidates)
	report.Counts["stable-candidates"] = len(stable)
	manifest.RGB.StableCandidates, err = store.JSON(candidateRows(stable))
	if err != nil {
		return report, err
	}
	phase("rgb-srgb")
	manifest.RGB.SRGB, err = store.Gzip(".srgb.gz", "application/octet-stream", func(w io.Writer) error { return derived.BuildSRGB(candidates, w) })
	if err != nil {
		return report, err
	}
	phase("rgb-txci")
	manifest.RGB.TXCI, err = store.Gzip(".txci.gz", "application/octet-stream", func(w io.Writer) error { return derived.BuildTXCI(candidates, w) })
	if err != nil {
		return report, err
	}
	candidates, stable = nil, nil
	phase("verify")
	if err := writeJSON(filepath.Join(output, "manifest.json"), manifest); err != nil {
		return report, err
	}
	if err := Verify(output); err != nil {
		return report, err
	}
	report.ManifestSHA256, err = input.FileSHA256(filepath.Join(output, "manifest.json"))
	if err != nil {
		return report, err
	}
	report.Missing = manifest.Missing
	report.Status = "ready"
	if len(manifest.Missing) != 0 {
		report.Status = "incomplete"
	}
	return report, nil
}

func readRows(filename string, expected int) ([]json.RawMessage, error) {
	file, err := os.Open(filename)
	if err != nil {
		return nil, err
	}
	defer file.Close()
	scanner := bufio.NewScanner(file)
	scanner.Buffer(make([]byte, 64<<10), 2<<20)
	rows := make([]json.RawMessage, 0, expected)
	seen := make(map[string]bool, expected)
	for scanner.Scan() {
		identity, _, row, err := decodeRow(scanner.Bytes())
		if err != nil {
			return nil, err
		}
		if seen[identity] {
			return nil, fmt.Errorf("duplicate metadata id: %s", identity)
		}
		seen[identity] = true
		canonical, err := json.Marshal(row)
		if err != nil {
			return nil, err
		}
		rows = append(rows, canonical)
		if len(rows) > expected {
			return nil, errors.New("metadata count exceeds declaration")
		}
	}
	if err := scanner.Err(); err != nil {
		return nil, err
	}
	if len(rows) != expected {
		return nil, errors.New("metadata count mismatch")
	}
	return rows, nil
}

func exportTextures(ctx context.Context, store *artifact.Store, content string) (map[string]Texture, error) {
	textures := make(map[string]Texture)
	identities := make(map[string]bool)
	err := filepath.WalkDir(filepath.Join(content, "Images"), func(filename string, entry fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if err := ctx.Err(); err != nil {
			return err
		}
		if entry.IsDir() {
			return nil
		}
		extension := strings.ToLower(filepath.Ext(filename))
		if extension != ".xnb" && extension != ".png" {
			return nil
		}
		file, err := os.Open(filename)
		if err != nil {
			return err
		}
		var img image.Image
		if extension == ".xnb" {
			img, err = xnb.Decode(file, 64<<20)
		} else {
			var config image.Config
			config, err = png.DecodeConfig(file)
			if err == nil && (config.Width <= 0 || config.Height <= 0 || int64(config.Width)*int64(config.Height) > 16<<20) {
				err = errors.New("PNG pixels exceed budget")
			}
			if err == nil {
				_, err = file.Seek(0, 0)
			}
			if err == nil {
				img, err = png.Decode(io.LimitReader(file, 64<<20))
			}
		}
		file.Close()
		if err != nil {
			return fmt.Errorf("decode %s: %w", entry.Name(), err)
		}
		object, err := store.Put(".png", "image/png", func(w io.Writer) error {
			return encodeTexturePNG(w, img)
		})
		if err != nil {
			return err
		}
		key, err := filepath.Rel(filepath.Join(content, "Images"), filename)
		if err != nil {
			return err
		}
		key = strings.TrimSuffix(filepath.ToSlash(key), filepath.Ext(key))
		if identities[strings.ToLower(key)] {
			return fmt.Errorf("duplicate texture name %s", key)
		}
		identities[strings.ToLower(key)] = true
		textures[key] = Texture{Width: img.Bounds().Dx(), Height: img.Bounds().Dy(), Object: object}
		return nil
	})
	return textures, err
}

func writeDimensions(filename string, textures map[string]Texture) error {
	file, err := os.OpenFile(filename, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
	if err != nil {
		return err
	}
	defer file.Close()
	keys := make([]string, 0, len(textures))
	for key := range textures {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	encoder := json.NewEncoder(file)
	for _, key := range keys {
		value := textures[key]
		if err := encoder.Encode(struct {
			ID     string `json:"id"`
			Width  int    `json:"width"`
			Height int    `json:"height"`
		}{key, value.Width, value.Height}); err != nil {
			return err
		}
	}
	return nil
}

func exportItems(store *artifact.Store, rows []json.RawMessage, semantics string, researchFamily, tooltipFamily, uiFamily, referencesFamily RuntimeFamily) ([]Pack, Pack, error) {
	if researchFamily.Path == "" || researchFamily.Count != len(rows) {
		return nil, Pack{}, errors.New("official research identity family missing or incomplete")
	}
	type researchIdentity struct {
		ID               int    `json:"id"`
		Required         int    `json:"required"`
		PersistentItemID int    `json:"persistentItemId"`
		PersistentID     string `json:"persistentId"`
	}
	researchRows, err := readRows(filepath.Join(semantics, researchFamily.Path), researchFamily.Count)
	if err != nil {
		return nil, Pack{}, err
	}
	researchIDs := make(map[int]researchIdentity, len(researchRows))
	for _, raw := range researchRows {
		var identity researchIdentity
		if err := json.Unmarshal(raw, &identity); err != nil {
			return nil, Pack{}, err
		}
		if identity.ID <= 0 || identity.PersistentItemID <= 0 || identity.PersistentID == "" || identity.Required < 0 {
			return nil, Pack{}, fmt.Errorf("invalid official research persistent ID for item %d", identity.ID)
		}
		if _, duplicate := researchIDs[identity.ID]; duplicate {
			return nil, Pack{}, fmt.Errorf("duplicate official research identity for item %d", identity.ID)
		}
		researchIDs[identity.ID] = identity
	}
	type itemTexture struct {
		asset      string
		sourceRect []int
	}
	textures := make(map[int]itemTexture)
	if referencesFamily.Path != "" {
		file, err := os.Open(filepath.Join(semantics, referencesFamily.Path))
		if err != nil {
			return nil, Pack{}, err
		}
		defer file.Close()
		scanner := bufio.NewScanner(file)
		scanner.Buffer(make([]byte, 64<<10), 2<<20)
		count := 0
		for scanner.Scan() {
			var reference struct {
				Kind       string          `json:"kind"`
				ID         int             `json:"numericId"`
				Asset      string          `json:"assetId"`
				SourceRect []int           `json:"sourceRect"`
				Animation  json.RawMessage `json:"animation"`
			}
			if err := json.Unmarshal(scanner.Bytes(), &reference); err != nil {
				return nil, Pack{}, err
			}
			count++
			if reference.Kind == "Item" {
				if _, exists := textures[reference.ID]; exists {
					return nil, Pack{}, errors.New("duplicate item texture binding")
				}
				if reference.Asset == "" {
					return nil, Pack{}, fmt.Errorf("item %d has empty texture binding", reference.ID)
				}
				animated := len(reference.Animation) > 0 && string(reference.Animation) != "null"
				if animated && (len(reference.SourceRect) != 4 || reference.SourceRect[0] < 0 || reference.SourceRect[1] < 0 || reference.SourceRect[2] <= 0 || reference.SourceRect[3] <= 0) {
					return nil, Pack{}, fmt.Errorf("item %d has invalid official animation frame", reference.ID)
				}
				textures[reference.ID] = itemTexture{asset: reference.Asset}
				if animated {
					textures[reference.ID] = itemTexture{asset: reference.Asset, sourceRect: reference.SourceRect}
				}
			}
		}
		if err := scanner.Err(); err != nil {
			return nil, Pack{}, err
		}
		if count != referencesFamily.Count {
			return nil, Pack{}, errors.New("texture binding count mismatch")
		}
	}
	tooltips := make(map[int]map[string]any)
	tooltipRows, err := readRows(filepath.Join(semantics, tooltipFamily.Path), tooltipFamily.Count)
	if err != nil {
		return nil, Pack{}, err
	}
	for _, raw := range tooltipRows {
		_, identity, row, err := decodeRow(raw)
		if err != nil {
			return nil, Pack{}, err
		}
		var id int
		if err := json.Unmarshal(identity, &id); err != nil {
			return nil, Pack{}, err
		}
		delete(row, "id")
		tooltips[id] = row
	}
	uiTooltips := make(map[int]json.RawMessage)
	if uiFamily.Path != "" {
		uiRows, err := readRows(filepath.Join(semantics, uiFamily.Path), uiFamily.Count)
		if err != nil {
			return nil, Pack{}, err
		}
		for _, raw := range uiRows {
			var identity struct {
				ID int `json:"id"`
			}
			if err := json.Unmarshal(raw, &identity); err != nil {
				return nil, Pack{}, err
			}
			uiTooltips[identity.ID] = raw
		}
	}
	var index []map[string]any
	type itemRow struct {
		id  int
		raw json.RawMessage
	}
	ordered := make([]itemRow, 0, len(rows))
	for _, raw := range rows {
		var identity struct {
			ID int `json:"id"`
		}
		if err := json.Unmarshal(raw, &identity); err != nil {
			return nil, Pack{}, err
		}
		if identity.ID <= 0 || identity.ID > 100000 {
			return nil, Pack{}, errors.New("invalid item identity")
		}
		ordered = append(ordered, itemRow{identity.ID, raw})
	}
	sort.Slice(ordered, func(i, j int) bool { return ordered[i].id < ordered[j].id })
	var packs []Pack
	var shard []map[string]any
	shardID := -1
	flush := func() error {
		if len(shard) == 0 {
			return nil
		}
		object, err := store.JSON(shard)
		if err != nil {
			return err
		}
		packs = append(packs, Pack{First: shardID * 256, Last: shardID*256 + 255, Rows: len(shard), Object: object})
		shard = nil
		debug.FreeOSMemory()
		return nil
	}
	for _, item := range ordered {
		_, _, row, err := decodeRow(item.raw)
		if err != nil {
			return nil, Pack{}, err
		}
		id := item.id
		research, found := researchIDs[id]
		if !found {
			return nil, Pack{}, fmt.Errorf("official research identity absent for item %d", id)
		}
		required, ok := row["research"].(json.Number)
		if !ok || required.String() != fmt.Sprint(research.Required) {
			return nil, Pack{}, fmt.Errorf("research count mismatch for item %d", id)
		}
		if id/256 != shardID {
			if err := flush(); err != nil {
				return nil, Pack{}, err
			}
			shardID = id / 256
		}
		texture := itemTexture{asset: fmt.Sprintf("Item_%d", id)}
		if referencesFamily.Path != "" {
			var exists bool
			texture, exists = textures[id]
			if !exists {
				return nil, Pack{}, fmt.Errorf("item %d has no texture binding", id)
			}
		}
		entry := map[string]any{"id": id, "internalName": row["internalName"], "persistentId": research.PersistentID, "name": row["name"], "texture": texture.asset}
		if len(texture.sourceRect) == 4 {
			entry["sourceRect"] = texture.sourceRect
		}
		entry["research"] = row["research"]
		delete(row, "research")
		if gameplay, ok := row["gameplay"].(map[string]any); ok {
			for _, key := range []string{"maxStack", "buffType", "headSlot", "bodySlot", "legSlot", "wingSlot", "mountType", "createTile", "createWall", "placeStyle", "handOnSlot", "handOffSlot", "backSlot", "frontSlot", "shoeSlot", "waistSlot", "shieldSlot", "neckSlot", "faceSlot", "balloonSlot", "beardSlot"} {
				if value, ok := gameplay[key]; ok {
					entry[key] = value
					delete(gameplay, key)
				}
			}
		}
		index = append(index, entry)
		delete(row, "name")
		delete(row, "internalName")
		row["tooltip"] = tooltips[id]
		if ui, ok := uiTooltips[id]; ok {
			row["uiTooltip"] = ui
		}
		shard = append(shard, row)
	}
	if err := flush(); err != nil {
		return nil, Pack{}, err
	}
	object, err := store.JSON(index)
	return packs, Pack{Rows: len(index), Object: object}, err
}

func readCandidates(filename string, count int) ([]derived.Candidate, error) {
	rows, err := readRows(filename, count)
	if err != nil {
		return nil, err
	}
	candidates := make([]derived.Candidate, 0, len(rows))
	for _, raw := range rows {
		var row struct {
			Kind          string
			Type, Variant int
			Paint         int
			Color         struct{ R, G, B int }
			Stable        bool
		}
		if err := json.Unmarshal(raw, &row); err != nil {
			return nil, err
		}
		if (row.Kind != "tile" && row.Kind != "wall") || row.Type < 0 || row.Type > 32767 || row.Variant < 0 || row.Variant > 65535 || row.Paint < 0 || row.Paint > 255 || row.Color.R < 0 || row.Color.R > 255 || row.Color.G < 0 || row.Color.G > 255 || row.Color.B < 0 || row.Color.B > 255 {
			return nil, errors.New("invalid painted map candidate")
		}
		kind := uint16(0)
		if row.Kind == "wall" {
			kind = 1
		}
		candidates = append(candidates, derived.Candidate{Kind: kind, Type: uint16(row.Type), Variant: uint16(row.Variant), Paint: uint8(row.Paint), R: uint8(row.Color.R), G: uint8(row.Color.G), B: uint8(row.Color.B), Stable: row.Stable})
	}
	return candidates, nil
}

// Compact rows share the exact order used by the corresponding binary index.
func candidateRows(candidates []derived.Candidate) [][8]uint16 {
	rows := make([][8]uint16, len(candidates))
	for i, c := range candidates {
		rows[i] = [8]uint16{c.Kind, c.Type, c.Variant, uint16(c.Paint), uint16(c.R), uint16(c.G), uint16(c.B), 0}
		if c.Stable {
			rows[i][7] = 1
		}
	}
	return rows
}

// Verify includes every transitive PNG reference, not just manifest packs.
func Verify(root string) error {
	file, err := os.Open(filepath.Join(root, "manifest.json"))
	if err != nil {
		return err
	}
	var manifest Manifest
	err = json.NewDecoder(io.LimitReader(file, 2<<20)).Decode(&manifest)
	file.Close()
	if err != nil {
		return err
	}
	if manifest.Schema != 1 || manifest.GameVersion == "" {
		return errors.New("unsupported or empty resource manifest")
	}
	for _, packs := range manifest.Families {
		for _, pack := range packs {
			if err := artifact.Verify(root, pack.Object); err != nil {
				return err
			}
		}
	}
	for _, object := range []artifact.Object{manifest.Textures, manifest.RGB.Candidates, manifest.RGB.StableCandidates, manifest.RGB.SRGB, manifest.RGB.TXCI} {
		if err := artifact.Verify(root, object); err != nil {
			return err
		}
	}
	var textures map[string]Texture
	if err := ReadObject(root, manifest.Textures, &textures); err != nil {
		return err
	}
	if err := validateTextureScope(manifest, len(textures)); err != nil {
		return err
	}
	if len(manifest.ImageBundles) > 0 {
		return VerifyImageBundles(root, manifest.ImageBundles, textures)
	}
	for _, texture := range textures {
		if texture.Width <= 0 || texture.Height <= 0 {
			return errors.New("invalid texture dimensions")
		}
		if err := artifact.Verify(root, texture.Object); err != nil {
			return err
		}
	}
	return nil
}
