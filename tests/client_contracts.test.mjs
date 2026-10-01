import test from 'node:test';
import assert from 'node:assert/strict';
import {validatePointer,validateManifestIdentity} from '../web/contracts.js';
const release='a'.repeat(64),previous='b'.repeat(64);
const pointer={schemaVersion:1,release,manifest:`releases/${release}/manifest.json`,manifestSha256:release,gameVersion:'0.0.2',previous};
const manifest={schemaVersion:1,gameVersion:'0.0.2',coverage:{complete:true}};
test('valid initial and subsequent channel identities',()=>{
  assert.equal(validatePointer(pointer),pointer);
  validatePointer({...pointer,previous:null});
  validatePointer({...pointer,previous:release}); // Reuploading identical bytes retains release identity.
  assert.equal(validateManifestIdentity(manifest,pointer),manifest);
});
for(const [name,value] of Object.entries({missingRelease:{...pointer,release:undefined},numericRelease:{...pointer,release:123},mismatchedHash:{...pointer,manifestSha256:previous},mismatchedPath:{...pointer,manifest:`releases/${previous}/manifest.json`},traversal:{...pointer,manifest:'releases/../manifest.json'},missingPrevious:{...pointer,previous:undefined},badVersion:{...pointer,gameVersion:'latest'},wrongSchema:{...pointer,schemaVersion:2},nullPointer:null,arrayPointer:[]})){
  test(`reject ${name}`,()=>assert.throws(()=>validatePointer(value),/版本指针/));
}
for(const [name,value] of Object.entries({wrongVersion:{...manifest,gameVersion:'0.0.1'},wrongSchema:{...manifest,schemaVersion:2},stringComplete:{...manifest,coverage:{complete:'true'}},missingCoverage:{...manifest,coverage:undefined},nullManifest:null})){
  test(`reject manifest ${name}`,()=>assert.throws(()=>validateManifestIdentity(value,pointer),/版本清单/));
}
