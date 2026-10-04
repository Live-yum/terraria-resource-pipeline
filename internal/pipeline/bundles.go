package pipeline

import (
	"archive/zip"
	"bytes"
	"compress/gzip"
	"crypto/sha256"
	"encoding/binary"
	"encoding/hex"
	"errors"
	"fmt"
	"image/png"
	"io"
	"os"
	"path/filepath"
	"sort"
	"strings"

	"terraria-resource-pipeline/internal/artifact"
)

const imageBundleMaxBytes = 16 << 20

type bundledImage struct {
	object artifact.Object
	width  int
	height int
}

type bundleLimitedWriter struct {
	destination io.Writer
	size        int64
}

func (w *bundleLimitedWriter) Write(data []byte) (int, error) {
	if int64(len(data)) > imageBundleMaxBytes-w.size {
		return 0, errors.New("image bundle exceeds 16 MiB")
	}
	n, err := w.destination.Write(data)
	w.size += int64(n)
	return n, err
}

func imageBundleBuckets(textures map[string]Texture) (map[string]map[string]bundledImage, error) {
	buckets := make(map[string]map[string]bundledImage)
	for _, texture := range textures {
		obj := texture.Object
		if texture.Width <= 0 || texture.Height <= 0 || len(obj.SHA256) != 64 || obj.SHA256 != strings.ToLower(obj.SHA256) || obj.MediaType != "image/png" || obj.Encoding != "" {
			return nil, errors.New("invalid bundled PNG descriptor")
		}
		if _, err := hex.DecodeString(obj.SHA256); err != nil {
			return nil, errors.New("invalid bundled PNG hash")
		}
		if obj.Path != "objects/"+obj.SHA256[:2]+"/"+obj.SHA256+".png" {
			return nil, errors.New("noncanonical bundled PNG path")
		}
		bucket := obj.SHA256[:2]
		if buckets[bucket] == nil {
			buckets[bucket] = make(map[string]bundledImage)
		}
		if previous, ok := buckets[bucket][obj.Path]; ok {
			if previous.object != obj || previous.width != texture.Width || previous.height != texture.Height {
				return nil, fmt.Errorf("conflicting texture descriptor %s", obj.Path)
			}
			continue
		}
		buckets[bucket][obj.Path] = bundledImage{object: obj, width: texture.Width, height: texture.Height}
	}
	return buckets, nil
}

// BuildImageBundles groups each distinct PNG object into one deterministic,
// stored ZIP entry in its SHA-256 prefix bucket. Gzip compresses the repeated
// ZIP paths and headers; PNG content remains byte-exact. No pixels are held.
func BuildImageBundles(store *artifact.Store, textures map[string]Texture) (map[string]artifact.Object, error) {
	if store == nil {
		return nil, errors.New("nil artifact store")
	}
	buckets, err := imageBundleBuckets(textures)
	if err != nil {
		return nil, err
	}
	result := make(map[string]artifact.Object, len(buckets))
	keys := make([]string, 0, len(buckets))
	for key := range buckets {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	for _, key := range keys {
		paths := make([]string, 0, len(buckets[key]))
		for path := range buckets[key] {
			paths = append(paths, path)
		}
		sort.Strings(paths)
		obj, err := store.Gzip(".zip.gz", "application/zip", func(destination io.Writer) error {
			limited := &bundleLimitedWriter{destination: destination}
			archive := zip.NewWriter(limited)
			for _, path := range paths {
				image := buckets[key][path]
				if err := artifact.Verify(store.Root, image.object); err != nil {
					archive.Close()
					return err
				}
				if image.object.Bytes > imageBundleMaxBytes {
					archive.Close()
					return errors.New("PNG exceeds image bundle limit")
				}
				header := &zip.FileHeader{Name: path, Method: zip.Store}
				header.SetMode(0644)
				entry, err := archive.CreateHeader(header)
				if err != nil {
					archive.Close()
					return err
				}
				file, err := os.Open(filepath.Join(store.Root, filepath.FromSlash(path)))
				if err != nil {
					archive.Close()
					return err
				}
				_, copyErr := io.Copy(entry, file)
				closeErr := file.Close()
				if copyErr != nil {
					archive.Close()
					return copyErr
				}
				if closeErr != nil {
					archive.Close()
					return closeErr
				}
			}
			return archive.Close()
		})
		if err != nil {
			return nil, fmt.Errorf("image bundle %s: %w", key, err)
		}
		result[key] = obj
	}
	return result, nil
}

// zipEntryCount inspects the bounded end-of-central-directory record before
// archive/zip allocates one File per claimed entry.
func zipEntryCount(file io.ReaderAt, size int64) (int, int64, error) {
	if size < 22 || size > imageBundleMaxBytes {
		return 0, 0, errors.New("invalid image bundle size")
	}
	endSize := int64(65557)
	if size < endSize {
		endSize = size
	}
	tail := make([]byte, endSize)
	if _, err := file.ReadAt(tail, size-endSize); err != nil {
		return 0, 0, err
	}
	for i := len(tail) - 22; i >= 0; i-- {
		if !bytes.Equal(tail[i:i+4], []byte{'P', 'K', 5, 6}) {
			continue
		}
		if i+22 != len(tail) || binary.LittleEndian.Uint16(tail[i+20:i+22]) != 0 {
			continue
		}
		if binary.LittleEndian.Uint16(tail[i+4:i+6]) != 0 || binary.LittleEndian.Uint16(tail[i+6:i+8]) != 0 {
			return 0, 0, errors.New("multi-disk image bundle")
		}
		count := binary.LittleEndian.Uint16(tail[i+10 : i+12])
		if count == 0xffff || count != binary.LittleEndian.Uint16(tail[i+8:i+10]) {
			return 0, 0, errors.New("unsupported image bundle entry count")
		}
		centralSize := binary.LittleEndian.Uint32(tail[i+12 : i+16])
		centralOffset := binary.LittleEndian.Uint32(tail[i+16 : i+20])
		if uint64(centralSize)+uint64(centralOffset) != uint64(size-endSize+int64(i)) {
			return 0, 0, errors.New("image bundle central directory offset mismatch")
		}
		var first [4]byte
		if _, err := file.ReadAt(first[:], 0); err != nil {
			return 0, 0, err
		}
		if count > 0 && first != [4]byte{'P', 'K', 3, 4} {
			return 0, 0, errors.New("image bundle contains leading data")
		}
		return int(count), int64(centralOffset), nil
	}
	return 0, 0, errors.New("image bundle lacks end of central directory")
}

func verifyZIPLayout(file io.ReaderAt, entries []*zip.File, centralOffset int64) error {
	offset := int64(0)
	for _, entry := range entries {
		if entry.Flags != 0x8 || entry.ModifiedDate != 0 || entry.ModifiedTime != 0 || len(entry.Extra) != 0 || entry.Comment != "" || !entry.Mode().IsRegular() {
			return fmt.Errorf("unsafe image bundle metadata: %s", entry.Name)
		}
		var header [30]byte
		if _, err := file.ReadAt(header[:], offset); err != nil {
			return err
		}
		if !bytes.Equal(header[:4], []byte{'P', 'K', 3, 4}) || binary.LittleEndian.Uint16(header[6:8]) != entry.Flags || binary.LittleEndian.Uint16(header[8:10]) != zip.Store || binary.LittleEndian.Uint16(header[10:12]) != 0 || binary.LittleEndian.Uint16(header[12:14]) != 0 || binary.LittleEndian.Uint16(header[28:30]) != 0 {
			return fmt.Errorf("unsafe image bundle local header: %s", entry.Name)
		}
		nameLen := int(binary.LittleEndian.Uint16(header[26:28]))
		if nameLen != len(entry.Name) {
			return errors.New("image bundle local name mismatch")
		}
		name := make([]byte, nameLen)
		if _, err := file.ReadAt(name, offset+30); err != nil {
			return err
		}
		if string(name) != entry.Name {
			return errors.New("image bundle local name mismatch")
		}
		dataStart := offset + 30 + int64(nameLen)
		actualStart, err := entry.DataOffset()
		if err != nil {
			return err
		}
		if actualStart != dataStart {
			return errors.New("image bundle contains hidden local metadata")
		}
		descriptorOffset := dataStart + int64(entry.CompressedSize64)
		if descriptorOffset+16 > centralOffset {
			return errors.New("image bundle entry overlaps central directory")
		}
		var descriptor [16]byte
		if _, err := file.ReadAt(descriptor[:], descriptorOffset); err != nil {
			return err
		}
		if !bytes.Equal(descriptor[:4], []byte{'P', 'K', 7, 8}) || binary.LittleEndian.Uint32(descriptor[4:8]) != entry.CRC32 || uint64(binary.LittleEndian.Uint32(descriptor[8:12])) != entry.CompressedSize64 || uint64(binary.LittleEndian.Uint32(descriptor[12:16])) != entry.UncompressedSize64 {
			return errors.New("image bundle data descriptor mismatch")
		}
		offset = descriptorOffset + 16
	}
	if offset != centralOffset {
		return errors.New("image bundle contains unreferenced data")
	}
	return nil
}

// VerifyImageBundles proves every referenced PNG occurs exactly once, checks
// ZIP entry safety/CRC/SHA-256, and reads only one entry at a time for dimensions.
func VerifyImageBundles(root string, bundles map[string]artifact.Object, textures map[string]Texture) error {
	buckets, err := imageBundleBuckets(textures)
	if err != nil {
		return err
	}
	if len(bundles) != len(buckets) {
		return errors.New("image bundle bucket count mismatch")
	}
	for key, images := range buckets {
		bundle, ok := bundles[key]
		if !ok {
			return fmt.Errorf("missing image bundle %s", key)
		}
		gzipBundle := bundle.Encoding == "gzip" && strings.HasSuffix(bundle.Path, ".zip.gz") && bundle.DecodedBytes >= 22 && bundle.DecodedBytes <= imageBundleMaxBytes
		rawBundle := bundle.Encoding == "" && strings.HasSuffix(bundle.Path, ".zip") && bundle.DecodedBytes == 0
		if bundle.Bytes > imageBundleMaxBytes || bundle.Bytes < 22 || bundle.MediaType != "application/zip" || (!gzipBundle && !rawBundle) {
			return fmt.Errorf("invalid image bundle descriptor %s", key)
		}
		if err := artifact.Verify(root, bundle); err != nil {
			return err
		}
		filename := filepath.Join(root, filepath.FromSlash(bundle.Path))
		file, err := os.Open(filename)
		if err != nil {
			return err
		}
		var zipData io.ReaderAt = file
		zipBytes := bundle.Bytes
		if gzipBundle {
			reader, openErr := gzip.NewReader(file)
			if openErr != nil {
				file.Close()
				return openErr
			}
			data, readErr := io.ReadAll(io.LimitReader(reader, bundle.DecodedBytes+1))
			closeErr := reader.Close()
			if readErr != nil || closeErr != nil || int64(len(data)) != bundle.DecodedBytes {
				file.Close()
				return errors.New("image bundle gzip data or decoded length mismatch")
			}
			zipData, zipBytes = bytes.NewReader(data), int64(len(data))
		}
		count, centralOffset, err := zipEntryCount(zipData, zipBytes)
		if err != nil {
			file.Close()
			return err
		}
		if count != len(images) {
			file.Close()
			return fmt.Errorf("image bundle %s entry count mismatch", key)
		}
		archive, err := zip.NewReader(zipData, zipBytes)
		if err != nil {
			file.Close()
			return err
		}
		if err := verifyZIPLayout(zipData, archive.File, centralOffset); err != nil {
			file.Close()
			return err
		}
		seen := make(map[string]bool, len(images))
		for _, entry := range archive.File {
			image, expected := images[entry.Name]
			if !expected || seen[entry.Name] || entry.Method != zip.Store || !entry.Mode().IsRegular() {
				file.Close()
				return fmt.Errorf("unexpected image bundle entry %q", entry.Name)
			}
			if entry.UncompressedSize64 != uint64(image.object.Bytes) || entry.CompressedSize64 != uint64(image.object.Bytes) {
				file.Close()
				return fmt.Errorf("image bundle entry size mismatch: %s", entry.Name)
			}
			seen[entry.Name] = true
			reader, err := entry.Open()
			if err != nil {
				file.Close()
				return err
			}
			limited := &io.LimitedReader{R: reader, N: image.object.Bytes + 1}
			hash := sha256.New()
			tee := io.TeeReader(limited, hash)
			config, err := png.DecodeConfig(tee)
			if err != nil {
				reader.Close()
				file.Close()
				return fmt.Errorf("image bundle PNG %s: %w", entry.Name, err)
			}
			_, copyErr := io.Copy(io.Discard, tee)
			closeErr := reader.Close()
			if copyErr != nil {
				file.Close()
				return fmt.Errorf("image bundle entry %s: %w", entry.Name, copyErr)
			}
			if closeErr != nil {
				file.Close()
				return closeErr
			}
			if limited.N != 1 || config.Width != image.width || config.Height != image.height || hex.EncodeToString(hash.Sum(nil)) != image.object.SHA256 {
				file.Close()
				return fmt.Errorf("image bundle PNG mismatch: %s", entry.Name)
			}
		}
		if len(seen) != len(images) {
			file.Close()
			return fmt.Errorf("image bundle %s lacks PNG entries", key)
		}
		if err := file.Close(); err != nil {
			return err
		}
	}
	for key := range bundles {
		if _, ok := buckets[key]; !ok {
			return fmt.Errorf("unexpected image bundle bucket %q", key)
		}
	}
	return nil
}
