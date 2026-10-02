// Small stable-channel contract. Hash verification alone cannot establish that
// the pointer's version and its manifest refer to the same immutable release.
const digestPattern=/^[a-f0-9]{64}$/;
export function validatePointer(pointer){
  if(!pointer||Array.isArray(pointer)||typeof pointer!=='object'||pointer.schemaVersion!==1
    ||typeof pointer.release!=='string'||!digestPattern.test(pointer.release)
    ||pointer.manifestSha256!==pointer.release
    ||pointer.manifest!==`releases/${pointer.release}/manifest.json`
    ||typeof pointer.gameVersion!=='string'||!/^\d+(?:\.\d+){2,3}$/.test(pointer.gameVersion)
    ||!(pointer.previous===null||(typeof pointer.previous==='string'&&digestPattern.test(pointer.previous))))
    throw Error('版本指针结构或身份不一致');
  return pointer;
}
export function validateManifestIdentity(manifest,pointer){
  validatePointer(pointer);
  if(!manifest||Array.isArray(manifest)||manifest.schemaVersion!==1
    ||manifest.gameVersion!==pointer.gameVersion||manifest.coverage?.complete!==true)
    throw Error('版本清单身份或覆盖不完整');
  return manifest;
}
