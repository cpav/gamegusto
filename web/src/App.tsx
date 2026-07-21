import { useCallback, useEffect, useState } from "react";
import { api, type Platform } from "./api";
import { AUTH_EXPIRED_EVENT, authConfig, completeSignIn, isSignedIn, signIn, signOut } from "./auth";
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
  // Incrementing this tells ChatView to start over.
  const [newChatNonce, setNewChatNonce] = useState(0);
  // Two-step confirm: clearing deletes the transcript from DynamoDB, and
  // there is no undo. A modal would be heavier than the action deserves;
  // asking in place costs one extra tap and cannot be mis-tapped.
  const [confirmingNew, setConfirmingNew] = useState(false);

  useEffect(() => {
    if (!confirmingNew) return;
    const timer = setTimeout(() => setConfirmingNew(false), 4000);
    return () => clearTimeout(timer);
  }, [confirmingNew]);

  useEffect(() => {
    completeSignIn()
      .catch(() => undefined)
      .finally(() => setAuthed(isSignedIn()));
  }, []);

  // A session can vanish while the app is open — iOS evicts storage from
  // installed PWAs that go unopened for a while. Without this the user would
  // sit on a screen whose every request quietly fails.
  useEffect(() => {
    const onExpired = () => setAuthed(false);
    window.addEventListener(AUTH_EXPIRED_EVENT, onExpired);
    return () => window.removeEventListener(AUTH_EXPIRED_EVENT, onExpired);
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

  const newChatButton = (
    <button
      className={"new-chat" + (confirmingNew ? " confirming" : "")}
      onClick={() => {
        if (!confirmingNew) {
          setConfirmingNew(true);
          return;
        }
        setConfirmingNew(false);
        setNewChatNonce((n) => n + 1);
        setTab("chat");
      }}
      aria-label={confirmingNew ? "Confirm: clear this conversation" : "Start a new conversation"}
      title="Start a new conversation"
    >
      {confirmingNew ? "Clear?" : "✨"}
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
          <Logo className="logo" />
          <h1>GAMEGUSTO</h1>
          <p>Your next game, cooked and served to your taste.</p>
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
        <button
          className={"sidebar-new" + (confirmingNew ? " confirming" : "")}
          onClick={() => {
            if (!confirmingNew) {
              setConfirmingNew(true);
              return;
            }
            setConfirmingNew(false);
            setNewChatNonce((n) => n + 1);
            setTab("chat");
          }}
        >
          <i>✨</i>
          {confirmingNew ? "Clear conversation?" : "New conversation"}
        </button>
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
          {/* Left of the wordmark, opposite the theme toggle: a persistent,
              obvious place for it rather than buried among suggestions. */}
          {newChatButton}
          <Logo className="logo" />
          <span className="wordmark">GAMEGUSTO</span>
          {themeButton}
        </header>

        {/* Both views stay mounted so switching tabs keeps scroll position
            and never interrupts a streaming answer. */}
        <div style={{ display: tab === "chat" ? "contents" : "none" }}>
          <ChatView
            onLibraryChanged={onLibraryChanged}
            onUsage={setUsage}
            newChatNonce={newChatNonce}
          />
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
