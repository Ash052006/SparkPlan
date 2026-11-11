import { FormEvent, KeyboardEvent, useRef } from "react";
import { MAX_IDEA_LENGTH } from "../api";
import { EXAMPLE_PROMPTS } from "../examples";

type Props = {
  idea: string;
  onIdeaChange: (value: string) => void;
  onSubmit: () => void;
  loading: boolean;
};

export default function PromptForm({ idea, onIdeaChange, onSubmit, loading }: Props) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const remaining = MAX_IDEA_LENGTH - idea.length;

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (loading || idea.trim().length === 0 || idea.length > MAX_IDEA_LENGTH) return;
    onSubmit();
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
      event.preventDefault();
      if (!loading && idea.trim().length > 0 && idea.length <= MAX_IDEA_LENGTH) {
        onSubmit();
      }
    }
  }

  function useExample(prompt: string) {
    onIdeaChange(prompt);
    textareaRef.current?.focus();
  }

  return (
    <section className="prompt-card prompt-card-awwwards" aria-labelledby="prompt-heading">
      <div className="prompt-heading">
        <div>
          <div className="prompt-badge-row">
            <span className="live-status-pill">
              <span className="live-dot" aria-hidden="true" />
              ESP32 Compiler Engine
            </span>
            <span className="capability-note">Deterministic CadQuery & Firmware</span>
          </div>
          <h2 id="prompt-heading" className="prompt-title">What do you want to create?</h2>
        </div>
      </div>

      <form onSubmit={submit} className="prompt-form">
        <label className="sr-only" htmlFor="idea">
          Hardware idea
        </label>
        <div className="prompt-input-wrapper">
          <textarea
            ref={textareaRef}
            id="idea"
            value={idea}
            onChange={(event) => onIdeaChange(event.target.value)}
            onKeyDown={handleKeyDown}
            maxLength={MAX_IDEA_LENGTH}
            rows={3}
            placeholder="e.g. A desk plant monitor with soil moisture probe and 0.96 inch OLED display..."
            aria-describedby="idea-help idea-count"
            disabled={loading}
            className="prompt-textarea"
          />
          <div className="prompt-input-glass-overlay" />
        </div>
        <div className="prompt-meta">
          <span id="idea-help" className="prompt-help-text">
            Natural language in. Checked 3D CAD, pinouts, and compilable firmware out.
          </span>
          <span id="idea-count" className={remaining < 40 ? "counter counter-warn" : "counter"}>
            {idea.length}/{MAX_IDEA_LENGTH}
          </span>
        </div>

        <div className="prompt-actions">
          <button
            className="primary-button prompt-submit-btn"
            type="submit"
            aria-label="Generate build"
            disabled={loading || idea.trim().length === 0 || idea.length > MAX_IDEA_LENGTH}
          >
            {loading ? (
              <span className="btn-loading-content">
                <span className="spinner-dot" /> Synthesizing Build...
              </span>
            ) : (
              <>
                <span>⚡ Generate Build</span>
                <kbd className="prompt-kbd" title="Press ⌘+Enter to generate">⌘↵</kbd>
              </>
            )}
          </button>
          <span className="prompt-footnote">Deterministic verification: zero hallucinated hardware.</span>
        </div>
      </form>

      <div className="examples" aria-label="Example prompts">
        <span className="examples-label">Instant Prototypes</span>
        <div className="example-chips">
          {EXAMPLE_PROMPTS.map((prompt, index) => (
            <button
              key={prompt}
              type="button"
              className="chip chip-glow"
              onClick={() => useExample(prompt)}
              disabled={loading}
              aria-label={`Use example ${index + 1}: ${prompt}`}
            >
              <span className="chip-prefix">✦</span> {prompt}
            </button>
          ))}
        </div>
      </div>
    </section>
  );
}
