package pipeline

import (
	"archive/zip"
	"bytes"
	"compress/gzip"
	"image"
	"image/color"
	"image/png"
	"io"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
	"time"

	"terraria-resource-pipeline/internal/artifact"
)

func bundleFixture(t *testing.T) (*artifact.Store, map[string]Texture, []byte) {
	t.Helper()
	store, err := artifact.New(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	picture := image.NewNRGBA(image.Rect(0, 0, 2, 1))
	picture.SetNRGBA(0, 0, color.NRGBA{R: 255, A: 255})
	picture.SetNRGBA(1, 0, color.NRGBA{G: 255, A: 255})
	var encoded bytes.Buffer
	if err := png.Encode(&encoded, picture); err != nil {
		t.Fatal(err)
	}
	obj, err := store.Put(".png", "image/png", func(w io.Writer) error { _, err := w.Write(encoded.Bytes()); return err })
	if err != nil {
		t.Fatal(err)
	}
	textures := map[string]Texture{"Tiles/1": {Width: 2, Height: 1, Object: obj}, "Tiles/2": {Width: 2, Height: 1, Object: obj}}
	return store, textures, encoded.Bytes()
}

func testZipObject(t *testing.T, store *artifact.Store, entries map[string][]byte) artifact.Object {
	t.Helper()
	obj, err := store.Put(".zip", "application/zip", func(w io.Writer) error {
		z := zip.NewWriter(w)
		for name, data := range entries {
			h := &zip.FileHeader{Name: name, Method: zip.Store}
			h.SetMode(0644)
			e, err := z.CreateHeader(h)
			if err != nil {
				return err
			}
			if _, err := e.Write(data); err != nil {
				return err
			}
		}
		return z.Close()
	})
	if err != nil {
		t.Fatal(err)
	}
	return obj
}

func TestImageBundlesDeterministicAndDeduplicated(t *testing.T) {
	store, textures, pngBytes := bundleFixture(t)
	first, err := BuildImageBundles(store, textures)
	if err != nil {
		t.Fatal(err)
	}
	second, err := BuildImageBundles(store, textures)
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(first, second) {
		t.Fatalf("nondeterministic bundles: %+v %+v", first, second)
	}
	if len(first) != 1 {
		t.Fatalf("one PNG should occupy one bucket: %d", len(first))
	}
	if err := VerifyImageBundles(store.Root, first, textures); err != nil {
		t.Fatal(err)
	}
	for _, bundle := range first {
		if bundle.Encoding != "gzip" || !strings.HasSuffix(bundle.Path, ".zip.gz") {
			t.Fatal("image bundle transport must compress ZIP headers")
		}
		file, err := os.Open(filepath.Join(store.Root, filepath.FromSlash(bundle.Path)))
		if err != nil {
			t.Fatal(err)
		}
		gz, err := gzip.NewReader(file)
		if err != nil {
			file.Close()
			t.Fatal(err)
		}
		decoded, err := io.ReadAll(gz)
		gz.Close()
		if err != nil || int64(len(decoded)) != bundle.DecodedBytes {
			file.Close()
			t.Fatal("ZIP gzip roundtrip failed")
		}
		z, err := zip.NewReader(bytes.NewReader(decoded), int64(len(decoded)))
		if err != nil {
			file.Close()
			t.Fatal(err)
		}
		if len(z.File) != 1 {
			file.Close()
			t.Fatalf("duplicate PNG stored %d times", len(z.File))
		}
		reader, err := z.File[0].Open()
		if err != nil {
			file.Close()
			t.Fatal(err)
		}
		got, err := io.ReadAll(reader)
		reader.Close()
		file.Close()
		if err != nil {
			t.Fatal(err)
		}
		if !bytes.Equal(got, pngBytes) {
			t.Fatal("offline PNG differs from source")
		}
		raw := testZipObject(t, store, map[string][]byte{textures["Tiles/1"].Object.Path: pngBytes})
		if err := VerifyImageBundles(store.Root, map[string]artifact.Object{textures["Tiles/1"].Object.SHA256[:2]: raw}, textures); err != nil {
			t.Fatalf("legacy raw ZIP no longer verifies: %v", err)
		}
	}
}

func TestVerifyImageBundlesRejectsMalformed(t *testing.T) {
	for _, which := range []string{"missing", "unexpected", "traversal", "crc", "dimension", "descriptor", "decoded-size", "decoded-budget", "encoding"} {
		t.Run(which, func(t *testing.T) {
			store, textures, pngBytes := bundleFixture(t)
			bundles, err := BuildImageBundles(store, textures)
			if err != nil {
				t.Fatal(err)
			}
			var key string
			for key = range bundles {
			}
			path := textures["Tiles/1"].Object.Path
			switch which {
			case "missing":
				delete(bundles, key)
			case "unexpected":
				bundles[key] = testZipObject(t, store, map[string][]byte{path: pngBytes, "objects/extra.png": pngBytes})
			case "traversal":
				bundles[key] = testZipObject(t, store, map[string][]byte{"../escape.png": pngBytes})
			case "crc":
				obj := testZipObject(t, store, map[string][]byte{path: pngBytes})
				data, err := os.ReadFile(filepath.Join(store.Root, filepath.FromSlash(obj.Path)))
				if err != nil {
					t.Fatal(err)
				}
				at := bytes.Index(data, pngBytes)
				if at < 0 {
					t.Fatal("PNG entry missing")
				}
				data[at+len(pngBytes)-8] ^= 1
				bundles[key], err = store.Put(".zip", "application/zip", func(w io.Writer) error { _, err := w.Write(data); return err })
				if err != nil {
					t.Fatal(err)
				}
			case "dimension":
				texture := textures["Tiles/1"]
				texture.Width = 3
				textures["Tiles/1"] = texture
				texture = textures["Tiles/2"]
				texture.Width = 3
				textures["Tiles/2"] = texture
			case "descriptor":
				obj := bundles[key]
				obj.Bytes++
				bundles[key] = obj
			case "decoded-size":
				obj := bundles[key]
				obj.DecodedBytes++
				bundles[key] = obj
			case "decoded-budget":
				obj := bundles[key]
				obj.DecodedBytes = imageBundleMaxBytes + 1
				bundles[key] = obj
			case "encoding":
				obj := bundles[key]
				obj.Encoding = "unknown"
				bundles[key] = obj
			}
			if err := VerifyImageBundles(store.Root, bundles, textures); err == nil {
				t.Fatalf("accepted malformed %s bundle", which)
			}
		})
	}
}

func TestImageBundleSizeLimit(t *testing.T) {
	store, err := artifact.New(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	chunk := make([]byte, 1<<20)
	obj, err := store.Put(".png", "image/png", func(w io.Writer) error {
		for i := 0; i < 17; i++ {
			if _, err := w.Write(chunk); err != nil {
				return err
			}
		}
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	_, err = BuildImageBundles(store, map[string]Texture{"large": {Width: 1, Height: 1, Object: obj}})
	if err == nil || !strings.Contains(err.Error(), "limit") {
		t.Fatalf("oversized bundle: %v", err)
	}
}

func TestVerifyImageBundlesRejectsUnsafeMetadata(t *testing.T) {
	for _, which := range []string{"symlink", "timestamp", "encrypted", "prefix"} {
		t.Run(which, func(t *testing.T) {
			store, textures, pngBytes := bundleFixture(t)
			path := textures["Tiles/1"].Object.Path
			var zipped bytes.Buffer
			archive := zip.NewWriter(&zipped)
			header := &zip.FileHeader{Name: path, Method: zip.Store}
			if which == "symlink" {
				header.SetMode(os.ModeSymlink | 0777)
			} else {
				header.SetMode(0644)
			}
			if which == "timestamp" {
				header.Modified = time.Date(2020, 1, 1, 0, 0, 0, 0, time.UTC)
			}
			entry, err := archive.CreateHeader(header)
			if err != nil {
				t.Fatal(err)
			}
			if _, err := entry.Write(pngBytes); err != nil {
				t.Fatal(err)
			}
			if err := archive.Close(); err != nil {
				t.Fatal(err)
			}
			data := zipped.Bytes()
			if which == "encrypted" {
				data = bytes.Clone(data)
				data[6] |= 1
				central := bytes.Index(data, []byte{'P', 'K', 1, 2})
				if central < 0 {
					t.Fatal("central header missing")
				}
				data[central+8] |= 1
			}
			if which == "prefix" {
				data = append([]byte{'x'}, data...)
			}
			bundle, err := store.Put(".zip", "application/zip", func(w io.Writer) error { _, err := w.Write(data); return err })
			if err != nil {
				t.Fatal(err)
			}
			bundles := map[string]artifact.Object{textures["Tiles/1"].Object.SHA256[:2]: bundle}
			if err := VerifyImageBundles(store.Root, bundles, textures); err == nil {
				t.Fatalf("accepted %s metadata", which)
			}
		})
	}
}
