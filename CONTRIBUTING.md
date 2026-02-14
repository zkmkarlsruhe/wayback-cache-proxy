# Contributing to Wayback Cache Proxy

Thank you for your interest in contributing!

---

## Ways to Contribute

### Report Bugs
- Use the **Issues** tab
- Include steps to reproduce
- Mention your environment (OS, Python version, Redis version)

### Suggest Features
- Open an issue with `enhancement` label
- Describe the use case

### Submit Code

1. **Fork** the repository
2. **Create a branch:** `git checkout -b feature/your-feature`
3. **Make changes** following code style
4. **Test** your changes
5. **Commit:** `git commit -m "Add feature: description"`
6. **Push:** `git push origin feature/your-feature`
7. **Open a Pull Request**

---

## Code Style

### Python
- Follow [PEP 8](https://pep8.org/)
- Use type hints
- Document with docstrings

### JavaScript (header bar, admin UI)
- Must be compatible with old browsers (IE5+)
- No `addEventListener`, `querySelector`, `const`/`let`, arrow functions
- Use `document.getElementById`, `onclick`, `var`, `innerHTML`
- Wrap in `<!-- -->` HTML comment for script hiding

---

## Commit Messages

- Present tense: "Add feature" not "Added feature"
- Reference issues: "Fix #123: Handle edge case"

---

## License

By contributing, you agree your contributions will be licensed under the project's [MIT License](LICENSE).

---

Thank you for helping improve ZKM open source!
