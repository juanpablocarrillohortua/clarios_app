// A stand-in for XMLHttpRequest, the one way the app sends a file
// (api/upload.ts). It sends nothing: a test plays the network by calling
// progress(), respond(), or fail() on the request the page opened.
import { vi } from "vitest";

export class FakeRequest {
  static opened: FakeRequest[] = [];

  method = "";
  url = "";
  headers: Record<string, string> = {};
  body: unknown = null;
  status = 0;
  responseText = "";
  aborted = false;
  upload: { onprogress: ((event: ProgressEvent) => void) | null } = { onprogress: null };
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onabort: (() => void) | null = null;

  open(method: string, url: string) {
    this.method = method;
    this.url = url;
    FakeRequest.opened.push(this);
  }

  setRequestHeader(name: string, value: string) {
    this.headers[name] = value;
  }

  send(body: unknown) {
    this.body = body;
  }

  abort() {
    this.aborted = true;
    this.onabort?.();
  }

  /** The browser reports `sent` of `total` bytes sent. */
  progress(sent: number, total: number) {
    this.upload.onprogress?.({ loaded: sent, total, lengthComputable: true } as ProgressEvent);
  }

  /** The server answers. */
  respond(status: number, body: unknown) {
    this.status = status;
    this.responseText = JSON.stringify(body);
    this.onload?.();
  }

  /** The connection breaks without an answer. */
  fail() {
    this.onerror?.();
  }
}

/** Replace XMLHttpRequest for one test; return the requests the page opens. */
export function fakeXhr(): typeof FakeRequest.opened {
  FakeRequest.opened = [];
  vi.stubGlobal("XMLHttpRequest", FakeRequest);
  return FakeRequest.opened;
}

/** A file of `size` bytes, made of nothing: only its size and name matter. */
export function fileOf(name: string, size: number, type = ""): File {
  const file = new File(["PK"], name, { type });
  Object.defineProperty(file, "size", { value: size });
  return file;
}
