import React, { useEffect, useState } from "react";

export interface User {
  user_id: string;
  email: string;
  name: string;
  avatar_url: string;
  provider: string;
  role: string;
}

export function UserMenu() {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const [modalOpen, setModalOpen] = useState(false);
  const [dropdownOpen, setDropdownOpen] = useState(false);

  useEffect(() => {
    fetch("/auth/me")
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        setUser(data);
        setLoading(false);
      })
      .catch(() => {
        setUser(null);
        setLoading(false);
      });
  }, []);

  const handleLogout = async () => {
    try {
      await fetch("/auth/logout", { method: "POST" });
      setUser(null);
      setDropdownOpen(false);
    } catch (e) {
      console.error("Logout failed:", e);
    }
  };

  const handleMockLogin = async () => {
    try {
      const res = await fetch("/auth/mock-login", { method: "POST" });
      if (res.ok) {
        const u = await res.json();
        setUser(u);
        setModalOpen(false);
      }
    } catch (e) {
      console.error("Mock login failed:", e);
    }
  };

  if (loading) {
    return <div className="user-menu-loading" aria-hidden="true" />;
  }

  return (
    <div className="user-menu-container">
      {user ? (
        <div className="user-profile-wrap">
          <button
            type="button"
            className="user-profile-btn"
            onClick={() => setDropdownOpen((v) => !v)}
            aria-expanded={dropdownOpen}
            title={user.name || user.email}
          >
            {user.avatar_url ? (
              <img src={user.avatar_url} alt={user.name} className="user-avatar-img" />
            ) : (
              <span className="user-avatar-fallback">
                {(user.name || user.email || "U").charAt(0).toUpperCase()}
              </span>
            )}
            <span className="user-name-label">{user.name || user.email.split("@")[0]}</span>
            <span className="user-chevron">▾</span>
          </button>

          {dropdownOpen && (
            <div className="user-dropdown-menu">
              <div className="user-dropdown-header">
                <strong>{user.name}</strong>
                <span className="muted user-dropdown-email">{user.email}</span>
                <span className="user-provider-tag">{user.provider}</span>
              </div>
              <div className="user-dropdown-divider" />
              <button
                type="button"
                className="user-dropdown-item logout-item"
                onClick={handleLogout}
              >
                Sign out
              </button>
            </div>
          )}
        </div>
      ) : (
        <button
          type="button"
          className="ghost-button auth-signin-btn"
          onClick={() => setModalOpen(true)}
        >
          Sign in
        </button>
      )}

      {modalOpen && (
        <div
          className="modal-backdrop"
          onClick={() => setModalOpen(false)}
          role="dialog"
          aria-modal="true"
          aria-labelledby="auth-modal-title"
        >
          <div className="modal auth-modal" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h2 id="auth-modal-title">Sign in to SparkPlan</h2>
              <button
                type="button"
                className="modal-close-btn"
                onClick={() => setModalOpen(false)}
                aria-label="Close"
              >
                ×
              </button>
            </div>
            <p className="auth-modal-desc">
              Sign in to unlock higher rate limits, cloud workspace sync, and physical silicon builds.
            </p>

            <div className="auth-providers-list">
              <a href="/auth/login/github" className="oauth-btn github-oauth-btn">
                <span className="oauth-icon">🐙</span>
                <span>Continue with GitHub</span>
              </a>

              <a href="/auth/login/google" className="oauth-btn google-oauth-btn">
                <span className="oauth-icon">🔍</span>
                <span>Continue with Google</span>
              </a>

              <div className="auth-or-divider">
                <span>or</span>
              </div>

              <button
                type="button"
                className="ghost-button dev-login-btn"
                onClick={handleMockLogin}
              >
                ⚡ Quick Dev Login (Offline Mode)
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default UserMenu;
