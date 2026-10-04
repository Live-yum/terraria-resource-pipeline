package pipeline

import (
	"bytes"
	"errors"
	"image"
	"image/color"
	"image/png"
	"io"
)

const maxIndexedPNGBytes = 16 << 20

var errPNGSizeLimit = errors.New("PNG candidate exceeds size limit")

type boundedPNGBuffer struct {
	bytes.Buffer
	limit int
}

func (b *boundedPNGBuffer) Write(p []byte) (int, error) {
	if len(p) > b.limit-b.Len() {
		return 0, errPNGSizeLimit
	}
	return b.Buffer.Write(p)
}

// encodeTexturePNG picks the smaller of an exact-color indexed PNG and the
// original true-color encoding. Both comparison buffers have a fixed ceiling.
func encodeTexturePNG(w io.Writer, img image.Image) error {
	const level = png.DefaultCompression
	original, ok := img.(*image.NRGBA)
	if !ok {
		return (&png.Encoder{CompressionLevel: level}).Encode(w, img)
	}
	indexed, ok := exactPalette(original)
	if !ok {
		return (&png.Encoder{CompressionLevel: level}).Encode(w, img)
	}
	paletted := &boundedPNGBuffer{limit: maxIndexedPNGBytes}
	if err := (&png.Encoder{CompressionLevel: level}).Encode(paletted, indexed); err != nil {
		if errors.Is(err, errPNGSizeLimit) {
			return (&png.Encoder{CompressionLevel: level}).Encode(w, img)
		}
		return err
	}
	trueColor := &boundedPNGBuffer{limit: paletted.Len()}
	if err := (&png.Encoder{CompressionLevel: level}).Encode(trueColor, img); err != nil {
		if !errors.Is(err, errPNGSizeLimit) {
			return err
		}
		_, err = io.Copy(w, bytes.NewReader(paletted.Bytes()))
		return err
	}
	_, err := io.Copy(w, bytes.NewReader(trueColor.Bytes()))
	return err
}

// exactPalette retains every NRGBA byte, including RGB under zero alpha, and
// assigns indices in first-pixel order. More than 256 distinct colors fall back.
func exactPalette(src *image.NRGBA) (*image.Paletted, bool) {
	b := src.Bounds()
	if b.Empty() {
		return nil, false
	}
	indices := make(map[color.NRGBA]uint8)
	palette := make(color.Palette, 0, 256)
	for y := b.Min.Y; y < b.Max.Y; y++ {
		row := src.Pix[src.PixOffset(b.Min.X, y):]
		for x := 0; x < b.Dx(); x++ {
			p := row[x*4:]
			c := color.NRGBA{R: p[0], G: p[1], B: p[2], A: p[3]}
			if _, exists := indices[c]; exists {
				continue
			}
			if len(palette) == 256 {
				return nil, false
			}
			indices[c] = uint8(len(palette))
			palette = append(palette, c)
		}
	}
	dst := image.NewPaletted(image.Rect(0, 0, b.Dx(), b.Dy()), palette)
	for y := b.Min.Y; y < b.Max.Y; y++ {
		row := src.Pix[src.PixOffset(b.Min.X, y):]
		out := dst.Pix[(y-b.Min.Y)*dst.Stride:]
		for x := 0; x < b.Dx(); x++ {
			p := row[x*4:]
			out[x] = indices[color.NRGBA{R: p[0], G: p[1], B: p[2], A: p[3]}]
		}
	}
	return dst, true
}
