import { Link } from "react-router-dom";
import "./advanced-research.css";

const destinations = [
  { title: "Results", description: "Review saved evaluation and ablation measurements.", href: "/artifacts" },
  { title: "Evaluations", description: "Run a validated benchmark evaluation.", href: "/experiments/evaluation" },
  { title: "Ablations", description: "Compare approved retrieval configurations.", href: "/experiments/ablation" },
  { title: "Jobs", description: "Inspect submitted research and preparation work.", href: "/jobs" },
  { title: "Index builds", description: "Review detailed build configuration and history.", href: "/index-builds" },
  { title: "Runtime overview", description: "Inspect local service and artifact summaries.", href: "/overview" },
];

export function AdvancedResearchPage() {
  return <section className="advanced-research" aria-labelledby="advanced-research-title">
    <header className="advanced-research__header">
      <p className="advanced-research__eyebrow">OPTIONAL TOOLS</p>
      <h1 id="advanced-research-title">Advanced Research</h1>
      <p>Evaluation and experiment tools for research workflows. Your saved measurements remain separate from Chat conversations.</p>
    </header>
    <nav className="advanced-research__links" aria-label="Advanced research tools">
      {destinations.map((destination) => <Link to={destination.href} key={destination.href}>
        <span><strong>{destination.title}</strong><small>{destination.description}</small></span>
        <span className="advanced-research__arrow" aria-hidden="true">→</span>
      </Link>)}
    </nav>
  </section>;
}
