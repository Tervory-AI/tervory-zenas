export async function resolve(specifier, context, nextResolve) {
  if (specifier === "@/lib/trigger-signin-fetch") {
    return { url: "data:text/javascript,export const signinFetch = (...args) => globalThis.fetch(...args);", shortCircuit: true };
  }
  if (specifier === "@/app/api/zenas/tank") {
    return nextResolve(new URL("../workspace/app/api/zenas/tank.ts", import.meta.url).href, context);
  }
  if (specifier.startsWith("./") || specifier.startsWith("../")) {
    const hasExtension = /\.(tsx?|jsx?|json|css|mjs|cjs)$/.test(specifier);
    if (!hasExtension) {
      for (const extension of [".ts", ".tsx"]) {
        try {
          return await nextResolve(specifier + extension, context);
        } catch {
          // Try the next extension the Market Lab sources use.
        }
      }
    }
  }
  return nextResolve(specifier, context);
}
