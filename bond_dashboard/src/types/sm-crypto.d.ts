// sm-crypto 无官方类型声明，这里补充最小声明
declare module "sm-crypto" {
  interface Sm4Options {
    mode?: "ecb" | "cbc";
    padding?: "pkcs#7" | "none";
    iv?: string | number[];
  }
  export const sm4: {
    encrypt(msg: string | Uint8Array, key: string | number[] | Uint8Array, options?: Sm4Options): string;
    decrypt(inArray: string | Uint8Array, key: string | number[] | Uint8Array, options?: Sm4Options): string;
  };
  export const sm2: unknown;
  export const sm3: unknown;
}
