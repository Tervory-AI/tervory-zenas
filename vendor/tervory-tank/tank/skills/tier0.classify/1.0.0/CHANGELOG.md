# tier0.classify

## 1.0.0
- First version. Prompt-only skill routed through policy `route.request`.
- 1.0.0: no output cap (Haiku returned nothing at 16 tokens); label normalised, unknown -> tier0.
- 1.0.0: prompt now defines each Squad role (the model cannot route to names it has never seen).
