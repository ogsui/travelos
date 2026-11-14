import next from "eslint-config-next/core-web-vitals";

const config = [
  ...next,
  {
    ignores: [
      "node_modules/**",
      ".next/**",
      "playwright-report/**",
      "test-results/**",
      "next-env.d.ts",
    ],
  },
];

export default config;
