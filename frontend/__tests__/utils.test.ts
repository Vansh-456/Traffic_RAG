import type { ChatMessage } from "@/lib/types";
import { applyEvent } from "@/lib/useChat";
import { splitCitations } from "@/lib/utils";

describe("splitCitations", () => {
  it("splits text and multi-id citations", () => {
    expect(splitCitations("Closed [1]. Diverted [2, 3]")).toEqual([
      { type: "text", value: "Closed " },
      { type: "cite", ids: [1] },
      { type: "text", value: ". Diverted " },
      { type: "cite", ids: [2, 3] },
    ]);
  });
});

describe("applyEvent", () => {
  it("accumulates tokens and applies revision", () => {
    let m: ChatMessage = { id: "a", role: "assistant", content: "" };
    m = applyEvent(m, { event: "token", data: { text: "Hel" } });
    m = applyEvent(m, { event: "token", data: { text: "lo" } });
    expect(m.content).toBe("Hello");
    m = applyEvent(m, { event: "revision", data: { text: "Hi" } });
    expect(m.content).toBe("Hi");
    expect(m.revised).toBe(true);
    m = applyEvent(m, { event: "done", data: { verified: true, follow_ups: ["q"] } });
    expect(m.done && m.verified).toBe(true);
    expect(m.followUps).toEqual(["q"]);
  });
});
