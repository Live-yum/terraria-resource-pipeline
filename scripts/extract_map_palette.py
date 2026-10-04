#!/usr/bin/env python3
"""Private data-only palette proof. Never a release or an approval command."""
from pathlib import Path
import argparse
import json
import sys

from resource_pipeline.map_palette_semantics import extract_map_palette_semantics
from resource_pipeline.security import PipelineError,atomic_write,canonical_json,sha256
from resource_pipeline.static_il import ILUnsupported


def private_output(value):
    raw=Path(value).absolute()
    if any(p.is_symlink() for p in (raw,*raw.parents)):
        raise PipelineError('Output cannot traverse symbolic links')
    output=raw.resolve();checkout=Path(__file__).resolve().parents[1]
    if output==checkout or checkout in output.parents:
        raise PipelineError('Private proof output must be outside the checkout')
    if output.exists() or not output.parent.is_dir():
        raise PipelineError('Output must be new and its parent must already exist')
    return output


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',required=True,type=Path)
    parser.add_argument('--xna',required=True,type=Path,help='fixed official XNA core DLL, read as data only')
    parser.add_argument('--output',required=True)
    args=parser.parse_args(argv)
    try:
        output=private_output(args.output)
        proof=extract_map_palette_semantics(args.source,xna_path=args.xna)
        raw=canonical_json(proof)
        summary={'status':proof['status'],'factScope':proof['factScope'],'executedInput':False,
                 'sourceProductionComplete':False,'consumerReleaseReady':False,'publicationApproved':False,
                 'wholeInitializerProven':False,'legendTailNonmutationProven':False,
                 'proofSha256':sha256(raw),'tileIds':len(proof['rows']['tiles']),
                 'wallIds':len(proof['rows']['walls']),
                 'tileOptions':sum(r['optionCount'] for r in proof['rows']['tiles']),
                 'wallOptions':sum(r['optionCount'] for r in proof['rows']['walls'])}
        # Recheck before creating anything; model failure leaves no output tree.
        if private_output(args.output)!=output:raise PipelineError('Output changed')
        output.mkdir(mode=0o700)
        atomic_write(output/'map-palette-proof.json',raw)
        atomic_write(output/'summary.json',canonical_json(summary))
        print(json.dumps(summary));return 0
    except (PipelineError,ILUnsupported,OSError,ValueError) as exc:
        print(json.dumps({'status':'REJECTED','executedInput':False,'error':str(exc)}));return 1


if __name__=='__main__':sys.exit(main())
