# LLM prompt templates

Each prompt is a UTF-8 text file `<name>.txt` in this folder (FR-34). Reel reads the file every time it uses it, so an edit applies to the next run without a restart (NFR-9).

- Placeholders use `$name` or `${name}` (Python `string.Template`), so JSON examples with `{ }` can be written as they are.
- Write `$$` for a literal dollar sign.
- File names use lowercase letters, digits and `_` only.

The prompt files are added together with the pipeline stages that use them: lesson boundaries (T-402), chapters and descriptions (T-501, T-502).
