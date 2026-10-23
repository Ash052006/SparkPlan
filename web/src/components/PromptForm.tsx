import { FormEvent, useRef } from "react";
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
    onSubmit();
  }

  function useExample(prompt: string) {
    onIdeaChange(prompt);
    textareaRef.current?.focus();
  }

  return (
    <section className="prompt-card" aria-labelledby="prompt-heading">
      <div className="prompt-heading">
        <div>
          <p className="eyebrow">Describe the build</p>
          <h2 id="prompt-heading">What do you want to make?</h2>
        </div>
        <span className="capability-note">ESP32 + catalog parts only</span>
      </div>

      <form onSubmit={submit} className="prompt-form">
        <label className="sr-only" htmlFor="idea">
          Hardware idea
        </label>
        <textarea
          ref={textareaRef}
          id="idea"
          value={idea}
          onChange={(event) => onIdeaChange(event.target.value)}
          maxLength={MAX_IDEA_LENGTH}
          rows={4}
          placeholder="e.g. a plant monitor that shows soil moisture on a small screen"
          aria-describedby="idea-help idea-count"
          disabled={loading}
        />
        <div className="prompt-meta">
          <span id="idea-help">Plain language is fine. SparkPlan will validate the result.</span>
          <span id="idea-count" className={remaining < 40 ? "counter counter-warn" : "counter"}>
            {idea.length}/{MAX_IDEA_LENGTH}
          </span>
        </div>

        <div className="prompt-actions">
          <button
            className="primary-button"
            type="submit"
            disabled={loading || idea.trim().length === 0 || idea.length > MAX_IDEA_LENGTH}
          >
            {loading ? "Generating..." : "Generate build"}
          </button>
          <span className="prompt-footnote">No physical build is performed.</span>
        </div>
      </form>

      <div className="examples" aria-label="Example prompts">
        <span className="examples-label">Try an example</span>
        <div className="example-chips">
          {EXAMPLE_PROMPTS.map((prompt, index) => (
            <button
              key={prompt}
              type="button"
              className="chip"
              onClick={() => useExample(prompt)}
              disabled={loading}
              aria-label={`Use example ${index + 1}: ${prompt}`}
            >
              {prompt}
            </button>
          ))}
        </div>
      </div>
    </section>
  );
}
