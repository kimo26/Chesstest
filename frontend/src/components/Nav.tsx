import { NavLink } from "react-router-dom";

const links = [
  { to: "/", label: "Dashboard" },
  { to: "/openings", label: "Openings" },
  { to: "/practice", label: "Practice" },
  { to: "/puzzles", label: "Puzzles" },
  { to: "/flashcards", label: "Flashcards" },
  { to: "/chat", label: "Coach" },
  { to: "/progress", label: "Progress" },
];

export default function Nav() {
  return (
    <nav className="nav">
      <div className="nav__brand">
        <span className="nav__icon">&#9822;</span> Chess Coach
      </div>
      <ul className="nav__links">
        {links.map((l) => (
          <li key={l.to}>
            <NavLink
              to={l.to}
              className={({ isActive }) =>
                `nav__link ${isActive ? "nav__link--active" : ""}`
              }
            >
              {l.label}
            </NavLink>
          </li>
        ))}
      </ul>
    </nav>
  );
}
