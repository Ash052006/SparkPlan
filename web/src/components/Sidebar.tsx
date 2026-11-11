import { ChatSession } from "../types/chat";

export interface SidebarProps {
  sessions: ChatSession[];
  activeSessionId: string;
  isOpen: boolean;
  onToggle: () => void;
  onSelectSession: (id: string) => void;
  onNewSession: () => void;
  onDeleteSession: (id: string) => void;
}

export default function Sidebar({
  sessions,
  activeSessionId,
  isOpen,
  onToggle,
  onSelectSession,
  onNewSession,
  onDeleteSession,
}: SidebarProps) {
  return (
    <>
      {/* Mobile Backdrop */}
      {isOpen && (
        <div
          className="sidebar-backdrop"
          onClick={onToggle}
          aria-hidden="true"
        />
      )}

      <aside
        className={`chatgpt-sidebar ${isOpen ? "open" : "collapsed"}`}
        aria-label="Conversation history"
      >
        <div className="sidebar-header">
          <button
            type="button"
            className="sidebar-new-btn"
            onClick={onNewSession}
            title="Start a new hardware build conversation"
          >
            <span className="sidebar-new-icon" aria-hidden="true">
              +
            </span>
            <span className="sidebar-new-text">New Build</span>
          </button>
          <button
            type="button"
            className="sidebar-toggle-btn"
            onClick={onToggle}
            aria-label={isOpen ? "Collapse sidebar" : "Expand sidebar"}
            title={isOpen ? "Collapse sidebar" : "Expand sidebar"}
          >
            {isOpen ? "◀" : "▶"}
          </button>
        </div>

        {isOpen && (
          <div className="sidebar-body">
            <div className="sidebar-section-title">Recent Builds</div>
            <div className="sidebar-list" role="list">
              {sessions.map((session) => {
                const isActive = session.id === activeSessionId;
                const turnCount = session.turns.length;
                return (
                  <div
                    key={session.id}
                    className={`sidebar-item ${isActive ? "active" : ""}`}
                    role="listitem"
                  >
                    <button
                      type="button"
                      className="sidebar-item-button"
                      onClick={() => onSelectSession(session.id)}
                      title={session.title}
                    >
                      <span className="sidebar-item-icon" aria-hidden="true">
                        💬
                      </span>
                      <span className="sidebar-item-title">{session.title}</span>
                      {turnCount > 0 && (
                        <span className="sidebar-turn-badge">{turnCount}</span>
                      )}
                    </button>
                    {sessions.length > 1 && (
                      <button
                        type="button"
                        className="sidebar-delete-btn"
                        onClick={(e) => {
                          e.stopPropagation();
                          onDeleteSession(session.id);
                        }}
                        aria-label={`Delete ${session.title}`}
                        title="Delete chat"
                      >
                        ×
                      </button>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        )}

        <div className="sidebar-footer">
          <div className="sidebar-status-pill">
            <span className="telemetry-dot" aria-hidden="true" />
            <span className="sidebar-footer-text">ESP32 Hardware Studio</span>
          </div>
        </div>
      </aside>
    </>
  );
}
