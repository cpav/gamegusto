import { useCallback, useEffect, useState } from "react";
import { api, type Platform } from "./api";
import { authConfig, completeSignIn, isSignedIn, signIn, signOut } from "./auth";
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
  const [platforms, setPlatforms] = useState<Platform[]>([]);
  const [usage, setUsage] = useState<string | null>(null);
  const [theme, setTheme] = useState<Theme>(
    () => (localStorage.getItem(THEME_KEY) as Theme | null) ?? "dark",
  );
  // `null` until the redirect from the hosted UI has been consumed, so the
  // app never flashes a sign-in screen at someone who is mid-login.
  const [authed, setAuthed] = useState<boolean | null>(null);

  useEffect(() => {
    completeSignIn()
      .catch(() => undefined)
      .finally(() => setAuthed(isSignedIn()));
  }, []);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem(THEME_KEY, theme);
  }, [theme]);

  useEffect(() => {
    api
      .platforms()
      .then((data) => setPlatforms(data.platforms))
      .catch(() => undefined);
  }, [reloadKey]);

  const onLibraryChanged = useCallback(() => setReloadKey((key) => key + 1), []);

  const themeButton = (
    <button
      className="theme-toggle"
      onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
      aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} mode`}
    >
      {theme === "dark" ? "☀" : "☾"}
    </button>
  );

  const nav = (
    <>
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
    </>
  );

  if (authed === null) {
    // Deliberately blank rather than a spinner: this resolves in a tick, and
    // a flash of loading chrome is worse than a moment of nothing.
    return <div className="app" />;
  }

  if (!authed) {
    return (
      <div className="app signin">
        <div className="signin-panel">
          <Logo className="logo" steam />
          <h1>GAMEGUSTO</h1>
          <p>Your next game, picked for tonight.</p>
          <button className="signin-button" onClick={() => void signIn()}>
            Sign in
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="app">
      {/* Desktop: the cabinet's side panel. Hidden on phones, where the
          marquee and the bottom tab bar carry the same information. */}
      <aside className="sidebar">
        <div className="brand">
          <Logo />
          GAMEGUSTO
        </div>
        <nav>{nav}</nav>
        <div className="label">Platforms</div>
        <div className="platform-chips">
          {platforms.length ? (
            platforms.map((platform) => <span key={platform.platform_id}>{platform.name}</span>)
          ) : (
            <span>none yet</span>
          )}
        </div>
        <div className="spacer" />
        {usage && <div className="usage-panel">{usage}</div>}
        <div className="sidebar-foot">
          {themeButton}
          {authConfig && (
            <button className="signout" onClick={signOut}>
              Sign out
            </button>
          )}
        </div>
      </aside>

      {/* The column the views live in. `display: contents` on phones so the
          shell stays one flex column; a real flex column beside the sidebar
          on desktop. */}
      <main className="main">
        <header className="marquee">
          <Logo className="logo" />
          <span className="wordmark">GAMEGUSTO</span>
          {themeButton}
        </header>

        {/* Both views stay mounted so switching tabs keeps scroll position
            and never interrupts a streaming answer. */}
        <div style={{ display: tab === "chat" ? "contents" : "none" }}>
          <ChatView onLibraryChanged={onLibraryChanged} onUsage={setUsage} />
        </div>
        <div style={{ display: tab === "library" ? "contents" : "none" }}>
          <LibraryView reloadKey={reloadKey} />
        </div>

        <nav className="dock">
          <div className="tabbar">{nav}</div>
        </nav>
      </main>
    </div>
  );
}
