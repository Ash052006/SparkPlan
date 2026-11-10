import React, { useState } from "react";

export interface ModifyPromptBarProps {
  onModify: (modification: string) => void;
  loading: boolean;
}

export function ModifyPromptBar({ onModify, loading }: ModifyPromptBarProps) {
  const [text, setText] = useState("");

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const trimmed = text.trim();
    if (!trimmed || loading) return;
    onModify(trimmed);
    setText("");
  };

  return (
    <div className="modify-bar-card">
      <div className="modify-bar-heading">
        <span className="modify-icon">💬</span>
        <div>
          <h4>Iterative Hardware Modification</h4>
          <p className="muted">
            Tweak this design conversationally without starting over. The planner will preserve
            existing pins and components where possible.
          </p>
        </div>
      </div>
      <form onSubmit={handleSubmit} className="modify-form">
        <input
          type="text"
          className="modify-input"
          placeholder="e.g. 'Add a passive buzzer that beeps when temp > 30°C' or 'Change display to LCD1602'"
          value={text}
          onChange={(e) => setText(e.target.value)}
          disabled={loading}
          maxLength={400}
        />
        <button
          type="submit"
          className="primary-button modify-submit-btn"
          disabled={!text.trim() || loading}
        >
          {loading ? "Modifying..." : "⚡ Update Build"}
        </button>
      </form>
    </div>
  );
}

export default ModifyPromptBar;
