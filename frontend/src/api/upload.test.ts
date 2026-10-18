// The one module that sends a file: the request it makes, its progress, and
// how it ends (an answer, a break, a stop).
import { afterEach, describe, expect, it, vi } from "vitest";

import { fakeXhr, fileOf } from "../test/xhr";
import { SendAborted, SendBroken, sendFile } from "./upload";

afterEach(() => {
  vi.unstubAllGlobals();
});

function start(signal = new AbortController().signal) {
  const opened = fakeXhr();
  const onProgress = vi.fn();
  const answer = sendFile({
    url: "/api/drafts?dataset_key=x",
    file: fileOf("Líneas & más.xlsx", 10),
    token: "a-token",
    onProgress,
    signal,
  });
  const [request] = opened;
  if (!request) throw new Error("nothing was sent");
  return { request, onProgress, answer };
}

describe("sending a file", () => {
  it("posts the file itself with the token and its encoded name", () => {
    const { request } = start();

    expect(request.method).toBe("POST");
    expect(request.headers.Authorization).toBe("Bearer a-token");
    expect(request.headers["X-File-Name"]).toBe("L%C3%ADneas%20%26%20m%C3%A1s.xlsx");
    expect(request.headers["Content-Type"]).toBe(
      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    );
    expect(request.body).toBeInstanceOf(File);
  });

  it("reports its progress and resolves with any answer", async () => {
    const { request, onProgress, answer } = start();

    request.progress(4, 10);
    request.respond(413, { code: "file_too_large" });

    expect(onProgress).toHaveBeenCalledWith({ sent: 4, total: 10 });
    await expect(answer).resolves.toEqual({ status: 413, body: { code: "file_too_large" } });
  });

  it("rejects when the connection breaks", async () => {
    const { request, answer } = start();

    request.fail();

    await expect(answer).rejects.toBeInstanceOf(SendBroken);
  });

  it("stops when its signal is aborted, and says so", async () => {
    const controller = new AbortController();
    const { request, answer } = start(controller.signal);

    controller.abort();

    expect(request.aborted).toBe(true);
    await expect(answer).rejects.toBeInstanceOf(SendAborted);
  });
});
