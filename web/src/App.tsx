import { useEffect, useState } from "react";
import { ChatView } from "./components/ChatView";
import { LibraryView } from "./components/LibraryView";
import { Logo } from "./components/Logo";

type Tab = "chat" | "library";
type Theme = "dark" | "light";

const THEME_KEY = "gg-theme";

export default function App() {
  const [tab, setTab] = useState<Tab>("chat");
  // Bumped whenever a chat turn may have changed stored data, so the library
  // refetches when you switch to it instead of showing a stale list.
  const [reloadKey, setReloadKey] = useState(0);
  const [theme, setTheme] = useState<Theme>(
    () => (localStorage.getItem(THEME_KEY) as Theme | null) ?? "dark",
  );

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem(THEME_KEY, theme);
  }, [theme]);

  return (
    <div className="app">
      <header className="marquee">
        <Logo className="logo" />
        <span className="wordmark">GAMEGUSTO</span>
        <button
          className="theme-toggle"
          onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
          aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} mode`}
        >
          {theme === "dark" ? "☀" : "☾"}
        </button>
      </header>

      {/* Both views stay mounted so switching tabs keeps scroll position and
          never interrupts a streaming answer. */}
      <div style={{ display: tab === "chat" ? "contents" : "none" }}>
        <ChatView onLibraryChanged={() => setReloadKey((key) => key + 1)} />
      </div>
      <div style={{ display: tab === "library" ? "contents" : "none" }}>
        <LibraryView reloadKey={reloadKey} />
      </div>

      <nav className="dock">
        <div className="tabbar">
          <button aria-current={tab === "chat" ? "page" : undefined} onClick={() => setTab("chat")}>
            <i>💬</i>Chat
          </button>
          <button
            aria-current={tab === "library" ? "page" : undefined}
            onClick={() => {
              setTab("library");
              setReloadKey((key) => key + 1);
            }}
          >
            <i>📚</i>Library
          </button>
        </div>
      </nav>
    </div>
  );
}
