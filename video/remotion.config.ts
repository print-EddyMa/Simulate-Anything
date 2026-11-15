import { Config } from "@remotion/cli/config";
// Use a local Chrome when Remotion's own download is not wanted: CHROME=/path/to/chrome
if (process.env.CHROME) Config.setBrowserExecutable(process.env.CHROME);
Config.setConcurrency(3);
