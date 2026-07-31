## Description

<!-- Provide a clear and concise description of what this PR does. -->

## Related Issue

<!-- If this PR fixes an issue, please link to it here. -->
Fixes #

## Type of Change

- [ ] Bug fix (non-breaking change which fixes an issue)
- [ ] New feature (non-breaking change which adds functionality)
- [ ] Breaking change (fix or feature that would cause existing functionality to not work as expected)
- [ ] This change requires a documentation update

## Testing

- [ ] Tests pass locally (`pytest tests/ -v -m "not mcp"`)
- [ ] 52/52 full-stack checks pass (`python final_verify.py`)
- [ ] k8s manifests valid (`python k8s_check2.py`)
- [ ] Bandit security scan clean
- [ ] flake8 passes
- [ ] pip-audit clean

## Checklist

- [ ] My code follows the style guidelines of this project
- [ ] I have performed a self-review of my code
- [ ] I have commented my code, particularly in hard-to-understand areas
- [ ] I have made corresponding changes to the documentation
- [ ] My changes generate no new warnings
- [ ] I have added tests that prove my fix is effective or that my feature works
- [ ] New and existing unit tests pass locally with my changes
