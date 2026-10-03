# Diagrams

The diagrams in the README and the docs are images rendered from the Mermaid sources here,
so they display the same in every Markdown viewer.

To change one, edit its `.mmd` file, then render:

```bash
python scripts/render_diagrams.py          # renders the diagrams whose source changed
```

Rendering needs Node.js and [mermaid-cli](https://github.com/mermaid-js/mermaid-cli)
(`npm i -g @mermaid-js/mermaid-cli`, or the script falls back to `npx`). Set
`PUPPETEER_EXECUTABLE_PATH` to use an installed Chrome or Chromium. Images are rendered at
2x and shown at half size, so they stay sharp on high-DPI screens.

`manifest.json` records the source each PNG was rendered from. The test suite fails when a
`.mmd` file changed without re-rendering, when a document references a missing image, or
when raw Mermaid code appears in the README or docs.

| Diagram | Shown in |
| --- | --- |
| `how-it-works` | README: How it works |
| `odd-authoring` | README: Drafting an ODD; docs/ontology.md |
| `laya-finetuning-loop` | README: Training Laya; docs/laya-finetuning.md |
| `odd-lifecycle`, `odd-structure` | README: ODD spec; docs/odd-spec.md |
| `choosing-a-sampler` | README: Scenario sampling; docs/sampling.md |
| `importance-sampling` | docs/sampling.md |
| `stl-scoring` | README: STL scoring; docs/stl-specs.md |
| `verdict-rule` | README: Verdicts; docs/verdicts.md |
| `runtime-monitors` | README: Runtime monitors; docs/runtime-monitors.md |
| `improvement-loop` | README: Improvement loop; docs/improvement-loop.md |
| `scenesmith-pipeline` | README: SceneSmith setup; docs/backends.md |
| `evidence-store` | README: Evidence store; docs/evidence-store.md |
| `index-schema` | docs/evidence-store.md |
| `secrets-flow` | README: API keys; docs/secrets.md |
