import { Composition } from "remotion";
import { Film, FPS, TOTAL } from "./Film";
import { Thumb } from "./Thumb";
export const Root = () => (
  <>
    <Composition id="Margin" component={Film} durationInFrames={TOTAL} fps={FPS} width={1920} height={1080} />
    <Composition id="Thumb" component={Thumb} durationInFrames={1} fps={30} width={1280} height={720} />
  </>
);
