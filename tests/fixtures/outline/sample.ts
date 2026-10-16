import { Bar } from "./bar";
import type { Baz } from "./baz";

export abstract class Foo extends Bar implements Baz {
  handle = async (x: number): Promise<void> => {
    await this.run(String(x));
  };

  run(a: string): void {
    console.log(a);
  }

  abstract go(): void;
}

export interface Store {
  get(id: string): string;
}

export const double = (a: number): number => a * 2;

function identity<T>(x: T): T {
  return x;
}
