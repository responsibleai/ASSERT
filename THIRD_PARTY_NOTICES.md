# Third-Party Notices

ASSERT builds on and credits the third-party open-source work listed below. We
are grateful to their authors and maintainers, and reproduce their original
copyright and license notices here as good-faith attribution.

---

## Bloom

ASSERT's automated behavioral-evaluation pipeline — its staged design of
generating scenarios from a specified behavior, running them against a target,
and judging the results — is **modeled on the design of Bloom**, an open-source
framework for automated behavioral evaluations of LLMs originally developed by
the Anthropic alignment team and released under the "Safety Research"
organization. ASSERT's own implementation (code, prompts, and configuration
schema) is independently written; this notice is provided as good-faith
attribution to the project whose design ASSERT follows, and to retain Bloom's
license notice.

- **Project:** Bloom — Automated Behavioral Evaluations for LLMs
- **Source:** https://github.com/safety-research/bloom
  (now developed and maintained by Meridian Labs at
  https://meridianlabs-ai.github.io/petri_bloom/)
- **License:** MIT

```
MIT License

Copyright (c) 2025 Safety Research

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

If you use ASSERT's behavioral-evaluation pipeline in research, please also cite
Bloom:

```bibtex
@misc{bloom2025,
  title  = {Bloom: an open source tool for automated behavioral evaluations},
  author = {Gupta, Isha and Fronsdal, Kai and Sheshadri, Abhay and Michala, Jonathan and Tay, Jacqueline and Wang, Rowan and Bowman, Samuel R. and Price, Sara},
  year   = {2025},
  url    = {https://github.com/safety-research/bloom},
}
```
