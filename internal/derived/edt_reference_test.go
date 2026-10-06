package derived

// legacyExactCube is the pre-optimization regression reference. It is not a game oracle.
// exactCube is the integer lower-envelope transform from stable-rgb-transform.cpp.
// dist and label are the only full-cube buffers (128 MiB total).
func legacyExactCube(sites []site, ranks []int32, dist, label []int32) {
	for i := range dist {
		dist[i] = infinity
		label[i] = -1
	}
	for _, s := range sites {
		at := int(s.code)
		if dist[at] != 0 || ranks[s.label] < ranks[label[at]] {
			dist[at] = 0
			label[at] = s.label
		}
	}
	for _, stride := range [...]int{1, 256, 65536} {
		var f, l [256]int32
		var vertex, start [256]int
		for outer := 0; outer < cubeSize; outer += stride * 256 {
			for inner := 0; inner < stride; inner++ {
				base := outer + inner
				for q := 0; q < 256; q++ {
					at := base + q*stride
					f[q] = dist[at]
					l[q] = label[at]
				}
				n := 0
				for q := 0; q < 256; q++ {
					if f[q] == infinity {
						continue
					}
					s := 0
					for n > 0 {
						p := vertex[n-1]
						a := int(f[q]) + q*q - int(f[p]) - p*p
						den := 2 * (q - p)
						s = a / den
						if a > 0 && a%den != 0 {
							s++
						}
						if a%den == 0 && ranks[l[q]] > ranks[l[p]] {
							s++
						}
						if s > start[n-1] {
							break
						}
						n--
					}
					if n == 0 {
						s = 0
					}
					if s < 256 {
						vertex[n] = q
						start[n] = s
						n++
					}
				}
				if n == 0 {
					continue
				}
				k := 0
				for x := 0; x < 256; x++ {
					for k+1 < n && start[k+1] <= x {
						k++
					}
					p := vertex[k]
					at := base + x*stride
					dist[at] = f[p] + int32((x-p)*(x-p))
					label[at] = l[p]
				}
			}
		}
	}
}
