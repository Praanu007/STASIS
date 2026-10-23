// The two checks the tests use; a failure throws with what was expected.
export function check(cond: unknown, what: string): asserts cond {
  if (!cond) throw new Error(what);
}
export function eq<T>(got: T, want: T, what: string): void {
  if (got !== want) throw new Error(`${what}: got ${JSON.stringify(got)}, want ${JSON.stringify(want)}`);
}
export type Test = [string, () => void | Promise<void>];
