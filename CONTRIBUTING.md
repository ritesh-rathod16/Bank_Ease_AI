# Contributing to BankEase AI

Thanks for your interest in improving BankEase AI. We welcome code, docs,
feedback, and issue reports from the community.

## Ways to contribute

- Report bugs or UI issues
- Suggest product or workflow improvements
- Improve documentation
- Submit fixes for frontend, backend, or local AI setup
- Add tests for regressions and edge cases

## Development workflow

1. Fork the repository.
2. Create a feature branch.
3. Install the project dependencies:

```powershell
npm install
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

4. Copy the environment example if needed:

```powershell
Copy-Item .env.example .env
```

5. Run the app locally:

```powershell
npm run dev:all
```

6. Make your change and validate it with the smallest relevant check.
7. Open a pull request with a clear explanation of the change and screenshots when applicable.

## Coding standards

- Keep changes focused and minimal.
- Avoid unrelated refactors.
- Preserve existing authentication, routing, and document processing behavior.
- Add or update tests when fixing a bug or changing logic.
- Prefer readable, maintainable code over clever shortcuts.

## Pull request expectations

- Include a clear title and summary
- Link any related issue if one exists
- Include screenshots for frontend changes when helpful
- Explain validation steps or testing performed

## Local setup notes

The project supports a local-first AI setup with Ollama. For Windows setup, see:

- [README.md](README.md)
- [docs/LOCAL_OLLAMA_SETUP.md](docs/LOCAL_OLLAMA_SETUP.md)

## Communication

For questions, use the GitHub discussions or issues in the repository. For
security-related disclosures, do not open a public issue; follow the process in
[SECURITY.md](SECURITY.md).
