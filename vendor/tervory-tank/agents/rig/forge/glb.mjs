// Embedded resources only. Never fetch URLs referenced by an uploaded asset.
import fs from 'node:fs/promises';
import {NodeIO} from '@gltf-transform/core';
import {ALL_EXTENSIONS, EXTMeshoptCompression} from '@gltf-transform/extensions';
import {dequantize} from '@gltf-transform/functions';
import {MeshoptDecoder, MeshoptEncoder} from 'meshoptimizer';
import validator from 'gltf-validator';

const [mode, source, destination] = process.argv.slice(2);
const bytes = new Uint8Array(await fs.readFile(source));
if (mode === 'validate') {
  const report = await validator.validateBytes(bytes, {
    maxIssues: 100,
    externalResourceFunction: async () => { throw new Error('External resources are forbidden'); },
  });
  await fs.writeFile(destination, JSON.stringify(report));
  if (report.issues.numErrors !== 0) process.exitCode = 1;
} else if (mode === 'decode' || mode === 'compress') {
  await MeshoptDecoder.ready;
  await MeshoptEncoder.ready;
  const io = new NodeIO().registerExtensions(ALL_EXTENSIONS)
    .registerDependencies({'meshopt.decoder': MeshoptDecoder, 'meshopt.encoder': MeshoptEncoder});
  const document = await io.readBinary(bytes);
  if (mode === 'decode') {
    for (const extension of document.getRoot().listExtensionsUsed()) {
      if (extension.extensionName === 'EXT_meshopt_compression') extension.dispose();
    }
    await document.transform(dequantize());
  } else {
    // gltfpack already quantized positions. Compress those bytes losslessly;
    // running the meshopt transform again would re-quantize integer positions.
    document.createExtension(EXTMeshoptCompression).setRequired(true)
      .setEncoderOptions({method: EXTMeshoptCompression.EncoderMethod.QUANTIZE});
  }
  await io.write(destination, document);
} else {
  throw new Error('Unknown operation');
}
