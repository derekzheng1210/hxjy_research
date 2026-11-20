(function () {
  'use strict';
  /* 登录凭据传输加密（渗透测试"传输过程信息泄漏"整改）。
   *
   * 内网 HTTP 属非安全上下文，浏览器不提供 crypto.subtle，因此这里自带
   * 纯 JS 实现：SHA-256（FIPS 180-4）+ RSA-OAEP-SHA256（BigInt 模幂）。
   * 与服务端 internal_knowledge_base/auth_crypto.py 配对使用：
   * GET /api/auth-challenge 取 {nonce, publicKey:{n,e}} →
   * RSA-OAEP 加密 JSON({密码字段…, nonce}) → POST {credential}。
   * 密码明文与等效摘要全程不出现在网络流量中；nonce 一次性，抓包重放无效。
   */

  // ---- SHA-256（输入/输出 Uint8Array）----
  const K = [
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
  ];

  function rotr(x, n) { return (x >>> n) | (x << (32 - n)); }

  function sha256(bytes) {
    const bitLen = bytes.length * 8;
    const padded = new Uint8Array((((bytes.length + 8) >> 6) + 1) << 6);
    padded.set(bytes);
    padded[bytes.length] = 0x80;
    const dv = new DataView(padded.buffer);
    dv.setUint32(padded.length - 4, bitLen >>> 0);
    dv.setUint32(padded.length - 8, Math.floor(bitLen / 0x100000000));
    let h0 = 0x6a09e667, h1 = 0xbb67ae85, h2 = 0x3c6ef372, h3 = 0xa54ff53a;
    let h4 = 0x510e527f, h5 = 0x9b05688c, h6 = 0x1f83d9ab, h7 = 0x5be0cd19;
    const w = new Uint32Array(64);
    for (let offset = 0; offset < padded.length; offset += 64) {
      for (let i = 0; i < 16; i++) w[i] = dv.getUint32(offset + i * 4);
      for (let i = 16; i < 64; i++) {
        const s0 = rotr(w[i - 15], 7) ^ rotr(w[i - 15], 18) ^ (w[i - 15] >>> 3);
        const s1 = rotr(w[i - 2], 17) ^ rotr(w[i - 2], 19) ^ (w[i - 2] >>> 10);
        w[i] = (w[i - 16] + s0 + w[i - 7] + s1) >>> 0;
      }
      let a = h0, b = h1, c = h2, d = h3, e = h4, f = h5, g = h6, h = h7;
      for (let i = 0; i < 64; i++) {
        const S1 = rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25);
        const ch = (e & f) ^ (~e & g);
        const t1 = (h + S1 + ch + K[i] + w[i]) >>> 0;
        const S0 = rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22);
        const maj = (a & b) ^ (a & c) ^ (b & c);
        const t2 = (S0 + maj) >>> 0;
        h = g; g = f; f = e; e = (d + t1) >>> 0;
        d = c; c = b; b = a; a = (t1 + t2) >>> 0;
      }
      h0 = (h0 + a) >>> 0; h1 = (h1 + b) >>> 0; h2 = (h2 + c) >>> 0; h3 = (h3 + d) >>> 0;
      h4 = (h4 + e) >>> 0; h5 = (h5 + f) >>> 0; h6 = (h6 + g) >>> 0; h7 = (h7 + h) >>> 0;
    }
    const out = new Uint8Array(32);
    const odv = new DataView(out.buffer);
    [h0, h1, h2, h3, h4, h5, h6, h7].forEach((v, i) => odv.setUint32(i * 4, v));
    return out;
  }

  // ---- 字节 / BigInt 转换与 RSA 模幂 ----
  function bytesToBigint(bytes) {
    let hex = '';
    for (const b of bytes) hex += b.toString(16).padStart(2, '0');
    return BigInt('0x' + (hex || '0'));
  }

  function bigintToBytes(value, length) {
    let hex = value.toString(16).padStart(length * 2, '0');
    const out = new Uint8Array(length);
    for (let i = 0; i < length; i++) out[i] = parseInt(hex.substr(i * 2, 2), 16);
    return out;
  }

  function modPow(base, exp, mod) {
    let result = 1n;
    let b = base % mod;
    let e = exp;
    while (e > 0n) {
      if (e & 1n) result = (result * b) % mod;
      b = (b * b) % mod;
      e >>= 1n;
    }
    return result;
  }

  function xorBytes(a, b) {
    const out = new Uint8Array(a.length);
    for (let i = 0; i < a.length; i++) out[i] = a[i] ^ b[i];
    return out;
  }

  function concatBytes(...arrays) {
    const total = arrays.reduce((sum, arr) => sum + arr.length, 0);
    const out = new Uint8Array(total);
    let offset = 0;
    for (const arr of arrays) { out.set(arr, offset); offset += arr.length; }
    return out;
  }

  // MGF1（SHA256 掩码生成函数，OAEP 标准组件）
  function mgf1(seed, length) {
    const out = new Uint8Array(length);
    let offset = 0;
    for (let counter = 0; offset < length; counter++) {
      const c = new Uint8Array(4);
      new DataView(c.buffer).setUint32(0, counter);
      const block = sha256(concatBytes(seed, c));
      out.set(block.subarray(0, Math.min(32, length - offset)), offset);
      offset += 32;
    }
    return out;
  }

  function rsaOaepEncrypt(publicKey, message) {
    const n = BigInt('0x' + publicKey.n);
    const e = BigInt('0x' + (publicKey.e || '10001'));
    const k = Math.ceil(publicKey.n.length / 2); // 模数字节数（hex 长度的一半）
    const hLen = 32;
    if (message.length > k - 2 * hLen - 2) throw new Error('加密内容过长');
    const seed = new Uint8Array(hLen);
    (window.crypto || window.msCrypto).getRandomValues(seed); // getRandomValues 在非安全上下文可用
    const lHash = sha256(new Uint8Array(0));
    const ps = new Uint8Array(k - message.length - 2 * hLen - 2);
    const db = concatBytes(lHash, ps, new Uint8Array([0x01]), message);
    const maskedDb = xorBytes(db, mgf1(seed, k - hLen - 1));
    const maskedSeed = xorBytes(seed, mgf1(maskedDb, hLen));
    const em = concatBytes(new Uint8Array([0x00]), maskedSeed, maskedDb);
    const cipher = modPow(bytesToBigint(em), e, n);
    return bigintToBytes(cipher, k);
  }

  function toBase64(bytes) {
    let binary = '';
    for (const b of bytes) binary += String.fromCharCode(b);
    return btoa(binary);
  }

  async function seal(payloadObj) {
    const challenge = await fetch('/internal-knowledge-base/api/auth-challenge', { credentials: 'same-origin' })
      .then(res => res.json());
    if (!challenge || !challenge.nonce || !challenge.publicKey) {
      throw new Error('安全通道暂不可用，请稍后重试');
    }
    const message = new TextEncoder().encode(JSON.stringify(Object.assign({}, payloadObj, {
      nonce: challenge.nonce,
    })));
    return toBase64(rsaOaepEncrypt(challenge.publicKey, message));
  }

  window.KbCrypto = { seal, sha256 };
})();
