import asyncio
import logging
import os
import re
from collections import defaultdict
from typing import Dict, List

import aiofiles
from testzeus_hercules.config import get_global_conf

# Filesystems cap a single name at NAME_MAX (255 bytes). The sanitized stem is
# clamped so that even a "_<n>" dedupe suffix plus ".feature" stays within it.
MAX_SCENARIO_FILENAME_STEM_LEN = 200


def _sanitize_scenario_filename(scenario_title: str) -> str:
    """
    Reduces a scenario title to a safe single path-component filename stem.

    Anything outside [A-Za-z0-9._-] (including "/" and "\\" path separators)
    becomes "_", dot runs collapse so ".."/"." cannot survive as a traversal
    reference, and leading/trailing dots are stripped. Returns "scenario" when
    nothing safe remains.
    """
    sanitized = re.sub(r"[^A-Za-z0-9._-]+", "_", scenario_title)
    sanitized = re.sub(r"\.{2,}", "_", sanitized).strip(".")
    return sanitized or "scenario"


def _line_above(content: str, offset: int) -> "tuple[int, str] | None":
    """
    Return ``(line_start, line_text)`` for the line sitting above ``offset``
    (which is expected to be at a line start), or None when ``offset`` is at
    the beginning of the content.
    """
    if offset <= 0:
        return None
    if content[offset - 1] != "\n":
        line_start = content.rfind("\n", 0, offset) + 1
        return line_start, content[line_start:offset]
    line_start = content.rfind("\n", 0, offset - 1) + 1
    return line_start, content[line_start : offset - 1]


def _leading_tag_block_start(content: str, offset: int, limit: int, prefixes: tuple) -> int:
    """
    Walk backwards from ``offset`` over consecutive lines that start with one of
    ``prefixes`` (tag "@..."/comment "#..." lines), crossing blank lines only to
    reach further prefixed lines. Returns the start offset of the topmost line
    in the block, never crossing ``limit``.
    """
    start = offset
    while start > limit:
        above = _line_above(content, start)
        if above is None or above[0] < limit:
            break
        if not above[1].strip().startswith(prefixes):
            break
        start = above[0]
        # Absorb blank lines directly above the block so the block stays whole.
        while start > limit:
            blank = _line_above(content, start)
            if blank is None or blank[0] < limit or blank[1].strip():
                break
            start = blank[0]
    return start


def _rule_block_start(content: str, gap_start: int, gap_end: int) -> "int | None":
    """
    Return the start offset of the first ``Rule:`` block located inside
    ``content[gap_start:gap_end]`` (including its own leading tag lines), or
    None when the gap contains no Rule.
    """
    match = re.search(r"^[\t ]*rule[\t ]*:.*$", content[gap_start:gap_end], re.IGNORECASE | re.MULTILINE)
    if match is None:
        return None
    return _leading_tag_block_start(content, gap_start + match.start(), gap_start, ("@",))


async def split_feature_file(input_file: str, output_dir: str, dont_append_header: bool = False) -> List[Dict[str, str]]:
    """
    Splits a single BDD feature file into multiple feature files asynchronously.
    The script preserves the feature-level content that should be shared across all scenario files.

    Parameters:
    input_file (str): Path to the input BDD feature file.
    output_dir (str): Path to the directory where the split feature files will be saved.
    dont_append_header (bool): If True, the Feature header is only added to the first extracted scenario file.

    Returns:
    list: A list of dictionaries containing feature, scenario, and output file path.
    """
    os.makedirs(output_dir, exist_ok=True)

    res_opt_list = []

    # utf-8-sig strips a leading BOM that would otherwise survive the read (and
    # be copied into every split file) or suppress the scenario regex match; a
    # plain locale-codec open mangles or crashes on CJK content under Windows.
    async with aiofiles.open(input_file, "r", encoding="utf-8-sig") as f:
        feature_content = await f.read()

    scenario_pattern = re.compile(r"^\s*scenario(?:\s+outline)?\s*:\s*([^\n]*)", re.IGNORECASE | re.MULTILINE)
    matches = list(scenario_pattern.finditer(feature_content))

    if not matches:
        return res_opt_list

    # The header ends above the first scenario's own leading tag/comment block,
    # so those lines stay with the first scenario instead of being stamped onto
    # every generated file.
    first_own_start = _leading_tag_block_start(feature_content, matches[0].start(), 0, ("@", "#"))
    feature_header = feature_content[:first_own_start].strip()

    feature_name = "Feature"
    for line in feature_header.split("\n"):
        if "Feature:" in line:
            feature_name = line.split("Feature:")[1].strip()
            break

    # Precompute each scenario's slice start: pulled back over the scenario's
    # own leading tag lines, and over an intervening Rule block (Rule line,
    # its tags and its Background) so rule-scoped setup travels with the
    # scenarios that follow it instead of lingering in the previous slice.
    slice_starts: List[int] = [first_own_start]
    rule_prefix = ""
    rule_headers: List[str] = [""]
    for i in range(1, len(matches)):
        own_start = _leading_tag_block_start(feature_content, matches[i].start(), matches[i - 1].end(), ("@",))
        rule_header = rule_prefix
        rule_start = _rule_block_start(feature_content, matches[i - 1].end(), matches[i].start())
        if rule_start is not None:
            rule_prefix = feature_content[rule_start:own_start].strip()
            own_start = min(own_start, rule_start)
            # This scenario's slice physically contains the Rule block already.
            rule_header = ""
        slice_starts.append(own_start)
        rule_headers.append(rule_header)

    prev_comment_lines = ""

    # Deduplicate on the SAFE title: distinct raw titles can collapse to the
    # same file name (e.g. "logs in!" and "logs in?"), and without a suffix the
    # second file would silently overwrite the first.
    already_visited_scenarios: Dict[str, int] = defaultdict(int)

    for i, match in enumerate(matches):
        slice_end = slice_starts[i + 1] if i + 1 < len(matches) else len(feature_content)
        scenario = feature_content[slice_starts[i] : slice_end].strip()
        comment_lines = ""
        comment_lines_li = []
        skip_line = 1
        for a_line in scenario.split("\n")[::-1]:
            line = a_line.strip()
            if line.startswith("#"):
                comment_lines = line + "\n" + comment_lines
                comment_lines_li.append(line)
            elif skip_line == 0:
                break
            else:
                skip_line -= 1

        scenario_title = match.group(1).strip() or match.group(0).strip()
        # Clamp the stem: an over-long title would exceed NAME_MAX once the
        # ".feature" suffix is appended and abort the ENTIRE split. The dedupe
        # below keys on the clamped stem, so distinct over-long titles sharing
        # the prefix still get distinct "_<n>"-suffixed files.
        safe_title = _sanitize_scenario_filename(scenario_title)[:MAX_SCENARIO_FILENAME_STEM_LEN]
        scenario_filename = f"{safe_title}.feature"
        output_file = os.path.join(output_dir, scenario_filename)

        for comment_line in comment_lines_li:
            scenario = scenario.replace(comment_line, "")

        if already_visited_scenarios[safe_title] > 0:

            scenario_filename = f"{safe_title}_{already_visited_scenarios[safe_title]}.feature"
            scenario_title = f"{scenario_title} - {already_visited_scenarios[safe_title]}"
            output_file = os.path.join(output_dir, scenario_filename)
        already_visited_scenarios[safe_title] += 1

        if dont_append_header and i > 0:
            parts = [prev_comment_lines, rule_headers[i], scenario]
        else:
            parts = [feature_header, prev_comment_lines, rule_headers[i], scenario]
        file_content = "\n\n".join(part for part in parts if part)

        # Write UTF-8 explicitly: scenario content may hold any Unicode, and the
        # locale codec would raise UnicodeEncodeError for CJK under e.g. cp1252.
        async with aiofiles.open(output_file, "w", encoding="utf-8") as f:
            await f.write(file_content)
        prev_comment_lines = comment_lines

        scenario_di = {
            "feature": feature_name,
            "scenario": scenario_title,
            "output_file": output_file,
        }
        res_opt_list.append(scenario_di)

    return res_opt_list


async def serialize_feature_file(file_path: str) -> str:
    """
    Converts a feature file to a single line string where new lines are replaced with ";next;".
    Any text starting from ";skip;" until the end of that line is excluded from the output.

    Parameters:
    file_path (str): Path to the feature file to be serialized.

    Returns:
    str: The serialized content of the feature file with skipped portions removed.
    """
    async with aiofiles.open(file_path, "r", encoding="utf-8-sig") as f:
        feature_content = await f.read()

    # Process each line to remove text from ";skip;" to the end of the line
    processed_lines = []
    for line in feature_content.split("\n"):
        skip_index = line.find(";skip;")
        if skip_index != -1:
            # Keep only the text before ";skip;"
            processed_lines.append(line[:skip_index])
        else:
            processed_lines.append(line)

    # Join the processed lines back together
    feature_content = "\n".join(processed_lines)

    # Continue with the normal processing
    while "  " in feature_content or "\n\n" in feature_content:
        feature_content = feature_content.replace("  ", " ")
        feature_content = feature_content.replace("\n\n", "\n")
    feature_content = feature_content.replace("\n", " ;next; ")
    return feature_content


async def process_feature_file(
    dont_append_header: bool = False,
) -> List[Dict[str, str]]:
    """
    Processes a Gherkin feature file by splitting it into smaller parts.

    Parameters:
        dont_append_header (bool): If True, the Feature header is only added to the first extracted scenario file.

    Returns:
        List[Dict[str, str]]: A list of dictionaries containing the split parts of the feature file.
    """
    tmp_gherkin_path = get_global_conf().get_tmp_gherkin_path()
    input_gherkin_file_path = get_global_conf().get_input_gherkin_file_path()

    return await split_feature_file(
        input_gherkin_file_path,
        tmp_gherkin_path,
        dont_append_header=dont_append_header,
    )


async def split_test(pass_background_to_all: bool = True) -> None:
    """
    Parses command line arguments and splits the feature file into individual scenario files.
    """
    # parser = argparse.ArgumentParser(
    #     description="Split a Gherkin feature file into individual scenario files."
    # )
    # parser.add_argument(
    #     "--feature_file_path", type=str, help="Path to the feature file to be split."
    # )
    # parser.add_argument(
    #     "--output_dir",
    #     type=str,
    #     help="Directory where the split scenario files will be saved.",
    # )
    # args = parser.parse_args()

    # feature_file_path = args.feature_file_path
    # output_dir = args.output_dir
    # list_of_feats = split_feature_file(feature_file_path, output_dir)
    logger = logging.getLogger(__name__)

    list_of_feats = await process_feature_file(dont_append_header=not pass_background_to_all)
    for feat in list_of_feats:
        file_path = feat["output_file"]
        logger.info(await serialize_feature_file(file_path))


# # Example usage
# if __name__ == "__main__":
#     asyncio.run(split_test())
