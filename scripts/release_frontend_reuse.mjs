// Build the candidate with the existing workspace tools while rejecting any
// resolved application source outside the candidate web tree.
import { createHash } from "node:crypto";
import { existsSync, lstatSync, mkdirSync, readFileSync, readdirSync, realpathSync, writeFileSync } from "node:fs";
import fs from "node:fs";
import fsPromises from "node:fs/promises";
import { createRequire, syncBuiltinESMExports } from "node:module";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath, pathToFileURL } from "node:url";

function args(values) {
  const parsed = {};
  for (let index = 0; index < values.length; index += 2) {
    const key = values[index];
    if (!["--candidate", "--dependency-root", "--report"].includes(key) || !values[index + 1] || parsed[key]) {
      throw new Error("Expected --candidate, --dependency-root and --report exactly once");
    }
    parsed[key] = values[index + 1];
  }
  if (Object.keys(parsed).length !== 3) throw new Error("Missing release frontend argument");
  return parsed;
}

function within(file, directory) {
  const relative = path.relative(directory, file);
  return relative === "" || (relative !== ".." && !relative.startsWith(`..${path.sep}`) && !path.isAbsolute(relative));
}

function digest(data) {
  return createHash("sha256").update(data).digest("hex");
}

const originalReadFileSync = readFileSync;

function existsOrLink(value) {
  try { lstatSync(value); return true; } catch (error) { if (error.code === "ENOENT") return false; throw error; }
}

function resolvedTarget(value) {
  let cursor = path.resolve(value);
  const missing = [];
  while (!existsOrLink(cursor)) {
    const parent = path.dirname(cursor);
    if (parent === cursor) break;
    missing.unshift(path.basename(cursor));
    cursor = parent;
  }
  return path.join(realpathSync(cursor), ...missing);
}

function parseJsonc(input) {
  let text = "", quoted = false, escaped = false, lineComment = false, blockComment = false;
  input = input.replace(/^\uFEFF/, "");
  for (let i = 0; i < input.length; i++) {
    const char = input[i], next = input[i + 1];
    if (lineComment) {
      if (char === "\n" || char === "\r") { lineComment = false; text += char; } else text += " ";
    } else if (blockComment) {
      if (char === "*" && next === "/") { blockComment = false; text += "  "; i++; }
      else text += char === "\n" || char === "\r" ? char : " ";
    } else if (quoted) {
      text += char;
      if (escaped) escaped = false;
      else if (char === "\\") escaped = true;
      else if (char === '"') quoted = false;
    } else if (char === '"') { quoted = true; text += char; }
    else if (char === "/" && next === "/") { lineComment = true; text += "  "; i++; }
    else if (char === "/" && next === "*") { blockComment = true; text += "  "; i++; }
    else text += char;
  }
  if (quoted || blockComment) throw new Error("Invalid TypeScript JSONC configuration");
  let clean = "";
  quoted = false;
  escaped = false;
  for (let i = 0; i < text.length; i++) {
    const char = text[i];
    if (quoted) {
      clean += char;
      if (escaped) escaped = false;
      else if (char === "\\") escaped = true;
      else if (char === '"') quoted = false;
    } else if (char === '"') { quoted = true; clean += char; }
    else if (char === "," && /^[\s]*[}\]]/.test(text.slice(i + 1))) { /* trailing comma */ }
    else clean += char;
  }
  return JSON.parse(clean);
}

function installWriteGuard(roots, violations) {
  function check(value) {
    if (typeof value === "number") return;
    let name = value instanceof URL ? fileURLToPath(value) : Buffer.isBuffer(value) ? value.toString() : value;
    if (typeof name !== "string") throw new Error("Unverifiable frontend write path");
    const target = resolvedTarget(name);
    if (!roots.some((root) => within(target, root))) {
      const error = new Error(`Frontend tool attempted to write outside candidate: ${target}`);
      violations.push(error);
      throw error;
    }
  }
  for (const object of [fs, fsPromises]) {
    for (const name of ["writeFile", "writeFileSync", "appendFile", "appendFileSync", "mkdir", "mkdirSync",
      "rm", "rmSync", "rmdir", "rmdirSync", "unlink", "unlinkSync", "truncate", "truncateSync",
      "chmod", "chmodSync", "chown", "chownSync", "utimes", "utimesSync", "createWriteStream"]) {
      if (typeof object[name] !== "function") continue;
      const original = object[name];
      object[name] = function (target, ...rest) { check(target); return original.call(this, target, ...rest); };
    }
    for (const name of ["rename", "renameSync", "copyFile", "copyFileSync", "cp", "cpSync"]) {
      if (typeof object[name] !== "function") continue;
      const original = object[name];
      object[name] = function (source, target, ...rest) {
        if (name.startsWith("rename")) check(source);
        check(target);
        return original.call(this, source, target, ...rest);
      };
    }
    for (const name of ["symlink", "symlinkSync", "link", "linkSync"]) {
      if (typeof object[name] !== "function") continue;
      object[name] = function () {
        const error = new Error("Frontend reuse must not create links");
        violations.push(error);
        throw error;
      };
    }
    for (const name of ["open", "openSync"]) {
      if (typeof object[name] !== "function") continue;
      const original = object[name];
      object[name] = function (target, flags, ...rest) {
        const writing = typeof flags === "string" ? /[wa+]/.test(flags) :
          typeof flags === "number" && Boolean(flags & (fs.constants.O_WRONLY | fs.constants.O_RDWR | fs.constants.O_CREAT | fs.constants.O_TRUNC | fs.constants.O_APPEND));
        if (writing) check(target);
        return original.call(this, target, flags, ...rest);
      };
    }
  }
  syncBuiltinESMExports();
}

async function main() {
  // Vite's config runner resolves a development environment before build().
  // Set this before loading the config so the runner cannot select dev React.
  process.env.NODE_ENV = "production";
  const options = args(process.argv.slice(2));
  const candidate = realpathSync(options["--candidate"]);
  const workspace = realpathSync(options["--dependency-root"]);
  const web = realpathSync(path.join(candidate, "apps", "web"));
  const dependencies = realpathSync(path.join(workspace, "node_modules"));
  if (candidate === workspace || !within(candidate, workspace) || !within(web, candidate)) {
    throw new Error("Candidate web root is outside the selected workspace candidate");
  }
  for (const relative of ["node_modules", "apps/node_modules", "apps/web/node_modules"]) {
    const entry = path.join(candidate, relative);
    if (existsOrLink(entry)) {
      throw new Error(`Candidate contains a dependency directory: ${relative}`);
    }
  }
  const output = path.join(web, "dist");
  if (existsOrLink(output)) throw new Error("Candidate frontend output already exists");
  const report = path.resolve(options["--report"]);
  if (existsOrLink(report)) throw new Error("Frontend source report already exists");
  const violations = [];
  installWriteGuard([web, path.join(candidate, ".local")], violations);
  const records = new Map();
  function track(raw, stage) {
    if (typeof raw !== "string" || !raw) throw new Error(`Invalid ${stage} module path`);
    if (raw.startsWith("\0")) return;
    let name = raw.startsWith("file:") ? fileURLToPath(raw) : raw;
    if (name.startsWith("/@fs/")) name = name.slice(4);
    name = name.split(/[?#]/, 1)[0];
    if (!path.isAbsolute(name) || !existsSync(name)) throw new Error(`Unverifiable ${stage} module: ${raw}`);
    const file = realpathSync(name);
    if (!within(file, web) && !within(file, dependencies)) {
      throw new Error(`${stage} resolved application source outside candidate and dependency roots: ${file}`);
    }
    const label = within(file, web)
      ? `candidate-web/${path.relative(web, file).split(path.sep).join("/")}`
      : `workspace-node-modules/${path.relative(dependencies, file).split(path.sep).join("/")}`;
    if (!lstatSync(file).isFile()) throw new Error(`${stage} resolved non-file module: ${file}`);
    if ((stage === "vite" || stage === "vite-output") && label.startsWith("workspace-node-modules/") &&
        (/(?:^|\/)(?:react|react-dom|scheduler)\/cjs\/[^/]*\.development\.js$/i.test(label) ||
         /(?:^|\/)react\/(?:jsx-dev-runtime(?:\.js)?|cjs\/react-jsx-dev-runtime(?:\.[^/]+)?\.js)$/i.test(label))) {
      throw new Error("Development React runtime cannot enter a production release graph");
    }
    records.set(`${stage}:${label}`, { stage, path: label, sha256: digest(originalReadFileSync(file)) });
  }

  const tsc = path.join(dependencies, "typescript", "bin", "tsc");
  track(tsc, "typescript-tool");
  const tscResolver = path.join(dependencies, "typescript", "lib", "getExePath.js");
  track(tscResolver, "typescript-tool");
  const executableResolver = await import(pathToFileURL(tscResolver).href);
  track(executableResolver.default(), "typescript-tool");
  const configs = new Set();
  const configInputs = new Set();
  function resolveConfigFile(name) {
    let result = path.resolve(name);
    if (!result.toLowerCase().endsWith(".json") && existsOrLink(result) && existsOrLink(`${result}.json`)) {
      throw new Error(`Ambiguous TypeScript configuration has both path and .json file: ${result}`);
    }
    if (existsOrLink(result) && lstatSync(result).isDirectory()) result = path.join(result, "tsconfig.json");
    if (!existsOrLink(result) && existsOrLink(`${result}.json`)) result += ".json";
    return realpathSync(result);
  }
  function checkConfigInput(name) {
    const configFile = resolveConfigFile(name);
    if (configInputs.has(configFile)) return;
    configInputs.add(configFile);
    if (configInputs.size > 200) throw new Error("Too many TypeScript configuration inputs");
    track(configFile, "typescript-config");
    const parsed = parseJsonc(originalReadFileSync(configFile, "utf8"));
    const extensions = parsed.extends === undefined ? [] : Array.isArray(parsed.extends) ? parsed.extends : [parsed.extends];
    for (const extension of extensions) {
      if (typeof extension !== "string" || !extension) throw new Error("Invalid TypeScript extends entry");
      let next;
      if (extension.startsWith(".") || path.isAbsolute(extension)) {
        next = path.resolve(path.dirname(configFile), extension);
      } else {
        const resolve = createRequire(configFile).resolve;
        for (const candidateName of [extension, `${extension}.json`, `${extension}/tsconfig.json`]) {
          try { next = resolve(candidateName); break; } catch { /* try the next standard form */ }
        }
        if (!next) throw new Error(`Cannot verify TypeScript extends: ${extension}`);
      }
      checkConfigInput(next);
    }
  }
  function checkTypeScriptConfig(configName) {
    const configFile = resolveConfigFile(configName);
    if (!within(configFile, web)) throw new Error(`TypeScript configuration resolved outside candidate: ${configFile}`);
    if (configs.has(configFile)) return;
    configs.add(configFile);
    if (configs.size > 100) throw new Error("Too many TypeScript project references");
    checkConfigInput(configFile);
    const shown = spawnSync(process.execPath, [tsc, "--showConfig", "-p", configFile], {
      cwd: web, encoding: "utf8", maxBuffer: 32 * 1024 * 1024, env: process.env,
    });
    if (shown.error) throw shown.error;
    if (shown.status !== 0) throw new Error(`Cannot inspect TypeScript config: ${shown.stdout}\n${shown.stderr}`);
    const parsed = JSON.parse(shown.stdout);
    const options = parsed.compilerOptions || {};
    const directory = path.dirname(configFile);
    const resolvedOption = (name) => options[name] ? path.resolve(directory, options[name]) : null;
    for (const name of ["outDir", "outFile", "declarationDir", "tsBuildInfoFile", "rootDir"]) {
      const value = resolvedOption(name);
      if (value && !within(resolvedTarget(value), web)) throw new Error(`TypeScript ${name} resolves outside candidate: ${value}`);
    }
    if (!options.tsBuildInfoFile && options.outDir && options.rootDir) {
      const defaultInfo = path.resolve(resolvedOption("outDir"), path.relative(resolvedOption("rootDir"), directory), `${path.basename(configFile, ".json")}.tsbuildinfo`);
      if (!within(resolvedTarget(defaultInfo), web)) throw new Error(`TypeScript build info resolves outside candidate: ${defaultInfo}`);
    }
    for (const reference of parsed.references || []) {
      if (typeof reference.path !== "string") throw new Error("Invalid TypeScript project reference");
      checkTypeScriptConfig(path.resolve(directory, reference.path));
    }
  }
  checkTypeScriptConfig(path.join(web, "tsconfig.json"));
  // TypeScript 7 delegates to a native binary, so JS write hooks cannot guard
  // its output. Inspect every resolved project config before invoking it and
  // use noEmit; build-info paths remain explicitly checked above.
  const result = spawnSync(process.execPath, [tsc, "-b", "--force", "--listFiles", "--noEmit"], {
    cwd: web, encoding: "utf8", maxBuffer: 64 * 1024 * 1024, env: process.env,
  });
  if (result.error) throw result.error;
  if (result.status !== 0) throw new Error(`Candidate TypeScript build failed:\n${result.stdout}\n${result.stderr}`);
  const typescriptFiles = result.stdout.split(/\r?\n/).filter(Boolean);
  if (typescriptFiles.length === 0) throw new Error("TypeScript did not report its resolved source graph");
  for (const file of typescriptFiles) track(file, "typescript");

  const vitePath = path.join(dependencies, "vite", "dist", "node", "index.js");
  track(vitePath, "vite-tool");
  // Vite can inline HTML/CSS assets without adding them to the module graph.
  // Observe its filesystem reads too, before importing Vite, and retain a
  // violation even if an upstream optional read catches the thrown error.
  const metadataPaths = new Set();
  for (let cursor = web; within(cursor, workspace); cursor = path.dirname(cursor)) {
    metadataPaths.add(path.join(cursor, "package.json"));
    if (cursor === workspace) break;
  }
  function checkRead(value) {
    if (typeof value === "number") return;
    let filename = value instanceof URL ? fileURLToPath(value) : Buffer.isBuffer(value) ? value.toString() : value;
    if (typeof filename !== "string") return;
    filename = path.resolve(filename);
    if (!existsSync(filename) || !lstatSync(filename).isFile() || metadataPaths.has(filename)) return;
    try { track(filename, "vite-read"); } catch (error) { violations.push(error); throw error; }
  }
  const originalAsyncRead = fsPromises.readFile;
  fsPromises.readFile = function (file, ...rest) { checkRead(file); return originalAsyncRead.call(this, file, ...rest); };
  fs.readFileSync = function (file, ...rest) { checkRead(file); return originalReadFileSync.call(this, file, ...rest); };
  syncBuiltinESMExports();
  const vite = await import(pathToFileURL(vitePath).href);
  const configPath = path.join(web, "vite.config.ts");
  const loaded = await vite.loadConfigFromFile({ command: "build", mode: "production" }, configPath, web, undefined, undefined, "runner");
  if (!loaded || !loaded.config) throw new Error("Cannot load candidate Vite configuration");
  track(loaded.path, "vite-config");
  for (const dependency of loaded.dependencies) track(dependency, "vite-config");
  const makeGuard = () => ({
    name: "candidate-source-boundary",
    enforce: "pre",
    configResolved(resolved) {
      if (resolved.command !== "build" || resolved.mode !== "production" || !resolved.isProduction || process.env.NODE_ENV !== "production") {
        throw new Error("Release frontend must remain a production build");
      }
      const actualRoot = realpathSync(resolved.root);
      const actualOutput = resolvedTarget(path.resolve(resolved.root, resolved.build.outDir));
      const actualCache = resolvedTarget(resolved.cacheDir);
      if (actualRoot !== web || actualOutput !== resolvedTarget(output) || !within(actualCache, path.join(candidate, ".local"))) {
        throw new Error("Vite configuration changed the candidate root, output or cache boundary");
      }
      if (resolved.publicDir) {
        const publicRoot = resolvedTarget(resolved.publicDir);
        if (!within(publicRoot, web)) throw new Error("Vite public directory resolves outside candidate web");
        function trackPublic(directory) {
          for (const entry of readdirSync(directory, { withFileTypes: true })) {
            const item = path.join(directory, entry.name);
            if (lstatSync(item).isSymbolicLink()) throw new Error("Vite public directory must not contain links");
            if (entry.isDirectory()) trackPublic(item);
            else if (entry.isFile()) track(item, "vite-public");
            else throw new Error("Vite public directory contains a non-file entry");
          }
        }
        if (existsOrLink(resolved.publicDir)) {
          if (lstatSync(resolved.publicDir).isSymbolicLink()) throw new Error("Vite public directory must not be a link");
          trackPublic(resolved.publicDir);
        }
      }
    },
    async resolveId(source, importer, options) {
      const resolved = await this.resolve(source, importer, { ...options, skipSelf: true });
      if (resolved && !resolved.external) track(resolved.id, "vite");
      return resolved;
    },
    load(id) { track(id, "vite"); },
    generateBundle(_options, bundle) {
      for (const id of this.getModuleIds()) track(id, "vite");
      if (typeof this.getWatchFiles === "function") {
        for (const id of this.getWatchFiles()) track(id, "vite-watch");
      }
      for (const output of Object.values(bundle)) {
        if (output.type === "chunk") {
          for (const id of Object.keys(output.modules)) track(id, "vite-output");
        }
      }
    },
  });
  const config = vite.mergeConfig(loaded.config, {
    configFile: false,
    mode: "production",
    root: web,
    cacheDir: path.join(candidate, ".local", "reuse-vite-cache"),
    plugins: [makeGuard()],
    build: { outDir: output, emptyOutDir: false },
  });
  // Workers are built by a separate Rolldown graph with its own plugin list.
  const userWorkerPlugins = config.worker?.plugins;
  config.worker = {
    ...config.worker,
    plugins: () => [typeof userWorkerPlugins === "function" ? userWorkerPlugins() : userWorkerPlugins || [], makeGuard()],
  };
  await vite.build(config);
  if (process.env.NODE_ENV !== "production") throw new Error("Release frontend must remain a production build");
  if (violations.length) throw violations[0];
  const files = [...records.values()].sort((a, b) => `${a.stage}:${a.path}`.localeCompare(`${b.stage}:${b.path}`));
  const value = {
    mode: "workspace-node-modules-candidate-web",
    typescript_files: files.filter((entry) => entry.stage === "typescript").length,
    vite_files: files.filter((entry) => entry.stage === "vite").length,
    source_graph_sha256: digest(JSON.stringify(files)),
    files,
  };
  mkdirSync(path.dirname(report), { recursive: true });
  writeFileSync(report, `${JSON.stringify(value, null, 2)}\n`, { encoding: "utf8", flag: "wx" });
  process.stdout.write(`${JSON.stringify({ typescript_files: value.typescript_files, vite_files: value.vite_files, source_graph_sha256: value.source_graph_sha256 })}\n`);
}

main().catch((error) => {
  process.stderr.write(`${error.stack || error}\n`);
  process.exitCode = 1;
});
