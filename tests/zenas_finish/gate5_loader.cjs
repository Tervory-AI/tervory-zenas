const ts = require(require.resolve('typescript', {paths:[process.cwd()+'/workspace']}));
module.exports = function(source) {
  return ts.transpileModule(source, {compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext,jsx:ts.JsxEmit.ReactJSX,esModuleInterop:true},fileName:this.resourcePath}).outputText;
};
