import { Moon, Sun } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";

export function ThemeToggle() {
  const [dark, setDark] = useState(() => document.documentElement.classList.contains("dark"));

  function toggle() {
    const next = !dark;
    document.documentElement.classList.toggle("dark", next);
    localStorage.setItem("theme", next ? "dark" : "light");
    setDark(next);
  }

  return (
    <Button variant="outline" onClick={toggle} aria-label={dark ? "Switch to light theme" : "Switch to dark theme"}>
      {dark ? <Sun size={16} /> : <Moon size={16} />}
      {dark ? "Light" : "Dark"}
    </Button>
  );
}
