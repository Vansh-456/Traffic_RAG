import { parseSse } from "@/lib/sse";

describe("parseSse", () => {
  it("parses complete events and keeps the remainder", () => {
    const { events, rest } = parseSse(
      'event: token\ndata: {"text":"Hi"}\n\n: ping\n\nevent: done\ndata: {}\n\nevent: tok',
    );
    expect(events).toEqual([
      { event: "token", data: { text: "Hi" } },
      { event: "done", data: {} },
    ]);
    expect(rest).toBe("event: tok");
  });

  it("handles CRLF line endings", () => {
    const { events } = parseSse('event: status\r\ndata: {"message":"x"}\r\n\r\n');
    expect(events[0]).toEqual({ event: "status", data: { message: "x" } });
  });
});
