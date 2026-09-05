import React from "react";
import { Plus, Send, ChevronDown, Phone, Mic } from "lucide-react";

function CallSummaryPill({ label, time }) {
  return (
    <div className="flex justify-center">
      <div
        data-testid="call-summary-pill"
        className="inline-flex items-center gap-2.5 bg-white border border-black/[0.06] rounded-full pl-2 pr-4 py-1.5 shadow-[0_1px_2px_rgba(0,0,0,0.03)]"
      >
        <span className="w-6 h-6 rounded-full bg-black/[0.04] flex items-center justify-center">
          <Phone
            className="w-3 h-3 text-[#4A4A48]"
            strokeWidth={1.75}
            fill="#4A4A48"
          />
        </span>
        <span className="text-[12px] text-[#1F1F1F] font-normal">
          {label}
        </span>
        <span className="text-[11px] text-[#9A9A98]">{time}</span>
      </div>
    </div>
  );
}

function DayDivider({ label }) {
  return (
    <div className="flex items-center justify-center py-1">
      <span className="text-[11px] font-normal text-[#9A9A98]">{label}</span>
    </div>
  );
}

function ActionSummary({ label }) {
  return (
    <div className="flex items-center justify-center py-2">
      <button
        data-testid="chat-action-summary"
        className="inline-flex items-center gap-1 text-[11.5px] text-[#9A9A98] hover:text-[#1F1F1F] transition-colors"
      >
        <span>{label}</span>
        <ChevronDown className="w-3 h-3" strokeWidth={1.75} />
      </button>
    </div>
  );
}

function EveMessage({ content }) {
  return (
    <div className="max-w-[92%] pr-2">
      <p className="text-[13.5px] leading-[1.65] text-[#1F1F1F] whitespace-pre-wrap font-normal">
        {content}
      </p>
    </div>
  );
}

function UserMessage({ content }) {
  return (
    <div className="flex justify-end">
      <div
        data-testid="user-message-bubble"
        className="max-w-[70%] bg-white border border-black/[0.06] rounded-2xl px-4 py-2 text-[13.5px] text-[#1F1F1F] font-normal shadow-[0_1px_2px_rgba(0,0,0,0.03)]"
      >
        {content}
      </div>
    </div>
  );
}

export default function ChatHub({
  chats,
  inputValue,
  setInputValue,
  onSend,
  sending,
  quickActions,
  onSuggestionClick,
  onMicClick,
}) {
  const scrollRef = React.useRef(null);

  React.useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [chats, sending]);

  return (
    <section
      data-testid="center-ai-hub"
      className="h-full w-full flex flex-col bg-[#FBFBF9] min-h-0"
    >
      {/* Scrollable chat feed */}
      <div
        ref={scrollRef}
        className="flex-1 overflow-y-auto eve-scroll px-6 pt-6 pb-4 space-y-5"
      >
        {chats.map((c) => {
          if (c.isDayDivider) return <DayDivider key={c.id} label={c.label} />;
          if (c.isCallSummary)
            return (
              <CallSummaryPill key={c.id} label={c.label} time={c.time} />
            );
          if (c.isActionSummary)
            return <ActionSummary key={c.id} label={c.label} />;

          return c.sender === "user" ? (
            <UserMessage key={c.id} content={c.content} />
          ) : (
            <EveMessage key={c.id} content={c.content} />
          );
        })}

        {sending && (
          <div
            data-testid="typing-indicator"
            className="flex items-center gap-1.5 pl-1"
          >
            <span className="w-1.5 h-1.5 rounded-full bg-[#B5B5B3] animate-pulse" />
            <span
              className="w-1.5 h-1.5 rounded-full bg-[#B5B5B3] animate-pulse"
              style={{ animationDelay: "120ms" }}
            />
            <span
              className="w-1.5 h-1.5 rounded-full bg-[#B5B5B3] animate-pulse"
              style={{ animationDelay: "240ms" }}
            />
          </div>
        )}
      </div>

      {/* Quick actions */}
      <div className="px-6 pt-1 pb-2 flex flex-wrap gap-2">
        {quickActions.map((q) => (
          <button
            key={q}
            data-testid={`quick-action-${q.replace(/\s+/g, "-").toLowerCase()}`}
            onClick={() => onSuggestionClick ? onSuggestionClick(q) : setInputValue(q)}
            className="text-[12px] px-3 py-1.5 rounded-full bg-white border border-black/[0.06] text-[#4A4A48] hover:border-black/[0.14] hover:text-[#1F1F1F] transition-colors font-normal"
          >
            {q}
          </button>
        ))}
      </div>

      {/* Composer */}
      <div className="px-6 pb-6 pt-2">
        <form
          onSubmit={onSend}
          data-testid="chat-input-area"
          className="flex items-center gap-2 bg-white border border-black/[0.06] rounded-full pl-3 pr-1.5 py-1.5 shadow-[0_2px_6px_rgba(0,0,0,0.03)] focus-within:border-black/[0.14] transition-colors"
        >
          <button
            type="button"
            className="w-7 h-7 rounded-full flex items-center justify-center text-[#9A9A98] hover:bg-black/[0.04]"
            data-testid="chat-attach-btn"
            aria-label="Attach"
          >
            <Plus className="w-4 h-4" strokeWidth={1.75} />
          </button>
          <input
            data-testid="chat-text-input"
            type="text"
            value={inputValue}
            onChange={(e) => setInputValue(e.target.value)}
            placeholder="Ask Eve anything…"
            className="flex-1 bg-transparent border-none text-[13.5px] text-[#1F1F1F] placeholder:text-[#B5B5B3] focus:outline-none py-1 font-normal"
            disabled={sending}
          />
          {onMicClick && (
            <button
              type="button"
              data-testid="chat-mic-btn"
              onClick={onMicClick}
              className="w-8 h-8 rounded-full flex items-center justify-center text-[#9A9A98] hover:bg-black/[0.04] transition-colors"
              aria-label="Voice intake"
            >
              <Mic className="w-4 h-4" strokeWidth={1.75} />
            </button>
          )}
          <button
            type="submit"
            data-testid="chat-send-btn"
            disabled={sending || !inputValue.trim()}
            className="w-8 h-8 rounded-full bg-[#1F1F1F] text-white flex items-center justify-center disabled:opacity-40 hover:bg-black transition-colors"
            aria-label="Send"
          >
            <Send className="w-3.5 h-3.5" strokeWidth={2} />
          </button>
        </form>
      </div>
    </section>
  );
}
