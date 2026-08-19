## End2End ASSERT Eval

<table>
  <thead>
    <tr>
      <th rowspan="3">Harm</th>
      <th colspan="4">With Template</th>
      <th colspan="4">Without Template</th>
    </tr>
    <tr>
      <th colspan="2">Prompt (Violations)</th>
      <th colspan="2">Scenario (Violations)</th>
      <th colspan="2">Prompt (Violations)</th>
      <th colspan="2">Scenario (Violations)</th>
    </tr>
    <tr>
      <th>Impermissible</th>
      <th>Permissible</th>
      <th>Impermissible</th>
      <th>Permissible</th>
      <th>Impermissible</th>
      <th>Permissible</th>
      <th>Impermissible</th>
      <th>Permissible</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td rowspan="1">violent_content</td>
      <td>18.1%</td>
      <td>6.7%</td>
      <td>14.5%</td>
      <td>2.7%</td>
      <td>3.6%</td>
      <td>0.7%</td>
      <td>8.4%</td>
      <td>1.3%</td>
    </tr>
    <tr>
      <td rowspan="1">relationship-entanglement</td>
      <td>10.9%</td>
      <td>43.1%</td>
      <td>44.8%</td>
      <td>36.7%</td>
      <td>17.2%</td>
      <td>24.1%</td>
      <td>40.9%</td>
      <td>22.9%</td>
    </tr>
  </tbody>
</table>


### Violent Content

![Grouped violation rates for violent content](figures/report-skill-v3/violent_content.svg)

### Relationship Entanglement

![Grouped violation rates for relationship entanglement](figures/report-skill-v3/relationship_entanglement.svg)


## Dimension Quality


| Harm | LLM pair diversity | Relevance mean (max 4) |
| --- | ---: | ---: |
| Relationship Entanglement | 0.9488 | 3.5152 |
| Violent Content | 0.9424 | 3.8519 |

### LLM Pair Diversity

![LLM pair diversity for imminent crisis management, relationship entanglement, and violent content](figures/report-skill-v3/global_llm_pair_diversity.svg)

### Relevance Mean

![Relevance mean on a 0–4 scale for imminent crisis management, relationship entanglement, and violent content](figures/report-skill-v3/global_relevance_mean.svg)
