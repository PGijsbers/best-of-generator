"""
Gallery view for a best-of list.

For each project, it shows an image (or automated homepage screenshot) and some
information. Note that only a selected subset of project information is shown
(compared to MarkdownListGenerator).
See the example at: https://github.com/jrieke/best-of-streamlit

Gallery view allows for some additional configuration args, all of which are optional:

skip_existing_screenshots (bool): Whether to skip taking homepage screenshots that
    were already taken before. Defaults to False.
skip_screenshots (bool): Whether to skip taking homepage screenshots completely.
    Defaults to False.
wait_before_screenshot (int): Seconds to wait before taking screenshot (so the website
    can load completely). Defaults to 10.
projects_per_category (int): Maximum number of projects that are shown per category.
    Defaults to 9.
projects_per_row (int): Maximum number of projects shown per row. Defaults to 3.
mobile_version (bool): Whether to create an additional, mobile-optimized version with
    only one column (i.e. projects_per_row=1). Defaults to False.
mobile_output_path (str): Output path of the mobile-optimized version. Defaults to
    "README-mobile.md".
mobile_markdown_header_file (str): Path to a mobile-specific header file. By default,
    the normal header file is used.
mobile_markdown_footer_file (str): Path to a mobile-specific footer file. By default,
    the normal header file is used.
short_toc (bool): Whether to use a short-form TOC that just takes up one line. Defaults
    to False. Only works when generate_toc is True.
"""

import asyncio
import logging
import os
import re
import time
from collections import OrderedDict
from datetime import datetime
from pathlib import Path
from typing import List

import pyppeteer
from addict import Dict

from best_of import default_config, utils
from best_of.generators import markdown_list
from best_of.generators.base_generator import BaseGenerator

log = logging.getLogger(__name__)


def chunker(seq, size):
    """Iterates over a sequence in chunks."""
    # From https://stackoverflow.com/questions/434287/what-is-the-most-pythonic-way-to-iterate-over-a-list-in-chunks
    return (seq[pos : pos + size] for pos in range(0, len(seq), size))


def shorten(s, max_len):
    """Shorten a string by appending ... if it's too long."""
    if len(s) > max_len:
        s = s[: max_len - 3] + "..."
    return s


async def save_screenshot(
    url: str, img_path: str, sleep: int = 5, width: int = 1024, height: int = 576
) -> None:
    """Loads url in headless browser and saves screenshot to file (.jpg or .png)."""
    browser = await pyppeteer.launch()
    page = await browser.newPage()
    await page.goto(url, {"timeout": 6000})  # increase timeout to 60 s for heroku apps
    await page.emulate({"viewport": {"width": width, "height": height}})
    time.sleep(sleep)
    # Type (PNG or JPEG) will be inferred from file ending.
    await page.screenshot({"path": img_path})
    await browser.close()


def generate_project_html(
    project: Dict, configuration: Dict, labels: Dict = None
) -> str:
    """Generates the content of a table cell for a project."""

    project_md = ""

    if project.image:
        img_path = project.image
    else:
        # Make screenshot of the homepage.
        screenshot_dir = Path("screenshots")
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        img_filename = "".join([c for c in project.name if c.isalpha()]) + ".png"
        img_path = screenshot_dir / img_filename

        if configuration.skip_screenshots:
            # Use existing img or default img if doesn't exist.
            if not img_path.exists():
                # TODO: Allow to set default screenshot via config.
                img_path = screenshot_dir / "0_default.png"
        elif not (configuration.skip_existing_screenshots and img_path.exists()):
            if project.homepage == project.github_url:
                # If no dedicated homepage is given (other than the github site),
                # use the default img.
                img_path = screenshot_dir / "0_default.png"
            else:
                # Try to take a screenshot of the website and use default img if that
                # fails.
                try:
                    # TODO: Could make this in parallel, but not really required right
                    #   now.
                    print(
                        f"Taking screenshot for {project.name} (from {project.homepage})"
                    )
                    sleep = configuration.get("wait_before_screenshot", 10)
                    asyncio.run(
                        save_screenshot(project.homepage, img_path, sleep=sleep)
                    )
                    print(f"Success! Saved in: {img_path}")
                except pyppeteer.errors.TimeoutError:
                    print(f"Timeout when loading: {project.homepage}")
                    img_path = screenshot_dir / "0_default.png"

    # TODO: Check that this link opens in new tab from Github readme.
    project_md += f'<br><a href="{project.homepage}"><img width="256" height="144" src="{img_path}"></a><br>'
    project_md += f'<h3><a href="{project.homepage}">{project.name}</a></h3>'

    metrics = []
    if project.created_at:
        project_total_month = utils.diff_month(datetime.now(), project.created_at)
        if (
            configuration.project_new_months
            and int(configuration.project_new_months) >= project_total_month
        ):
            metrics.append("🐣 New")
    if project.star_count:
        metrics.append(f"⭐ {str(utils.simplify_number(project.star_count))}")
    if project.github_url:
        metrics.append(f'<a href="{project.github_url}">:octocat: Code</a>')

    if metrics:
        metrics_str = " · ".join(metrics)
        project_md += f"<p>{metrics_str}</p>"

    description = project.description
    if description[-1] == ".":  # descriptions returned by best-of end with .
        description = description[:-1]
    description = shorten(description, 90)
    project_md += f"<p>{description}</p>"

    if project.github_id:
        author = project.github_id.split("/")[0]
        project_md += (
            f'<p><sup>by <a href="https://github.com/{author}">@{author}</a></sup></p>'
        )

    return project_md


def generate_table_html(projects: list, config: Dict, labels: Dict) -> str:
    """Generates a table containing several projects."""
    table_html = '<table width="100%">'
    print("Creating table...")
    for project_row in chunker(projects, config.get("projects_per_row", 3)):
        print("New row:")
        table_html += '<tr align="center">'
        for project in project_row:
            print("- " + project.name)
            # table_html += project.name
            project_md = generate_project_html(project, config, labels)
            table_html += f'<td valign="top" width="33.3%">{project_md}</td>'
        table_html += "</tr>"
    table_html += "</table>"
    print()
    return table_html


def generate_category_gallery_md(
    category: Dict, config: Dict, labels: list, title_md_prefix: str = "##"
) -> str:
    """Generates markdown gallery for a category, containing tables with projects."""
    category_md = ""

    if (
        (
            config.hide_empty_categories
            or category.category == default_config.DEFAULT_OTHERS_CATEGORY_ID
        )
        and not category.projects
        and not category.hidden_projects
    ):
        # Do not show category
        return category_md

    # Set up category header.
    category_md += title_md_prefix + " " + category.title + "\n\n"
    # TODO: Original line doesn't work if there's no TOC. Replaced it with link
    #   to title for now but fix this in original repo.
    # category_md += f'<a href="#contents"><img align="right" width="15" height="15" src="{best_of.default_config.UP_ARROW_IMAGE}" alt="Back to top"></a>\n\n'
    category_md += f'<a href="#----best-of-streamlit----"><img align="right" width="15" height="15" src="{default_config.UP_ARROW_IMAGE}" alt="Back to top"></a>\n\n'
    if category.subtitle:
        category_md += "_" + category.subtitle.strip() + "_\n\n"

    if category.projects:
        # Show top projects directly (in a html table).
        num_shown = config.get("projects_per_category", 6)
        table_html = generate_table_html(category.projects[:num_shown], config, labels)
        category_md += table_html + "\n\n"

        # Hide other projects in an expander.
        if len(category.projects) > num_shown:
            hidden_table_html = generate_table_html(
                category.projects[num_shown:], config, labels
            )
            category_md += f'<br><details align="center"><summary><b>Show {len(category.projects) - num_shown} more for "{category.title}"</b></summary><br>{hidden_table_html}</details>\n\n'

    # This is actually not used here (because all projects are set to show:
    # True) but it's left here from the original `best_of.generate_category_md` function
    # for completeness.
    if category.hidden_projects:
        category_md += (
            "<details><summary>Show "
            + str(len(category.hidden_projects))
            + " hidden projects...</summary>\n\n"
        )
        for project in category.hidden_projects:
            project_md = markdown_list.generate_project_md(
                project, config, labels, generate_body=False
            )
            category_md += project_md + "\n"
        category_md += "</details>\n"
    # print(category_md)
    return "<br>\n\n" + category_md


def process_md_link(text: str) -> str:
    text = text.lower().replace(" ", "-")
    return re.compile(r"[^a-zA-Z0-9-]").sub("", text)


def generate_short_toc(categories: OrderedDict, config: Dict) -> str:
    toc_md = ""
    toc_points = []
    for category in categories:
        category_info = Dict(categories[category])
        if category_info.ignore:
            continue

        url = "#" + process_md_link(category_info.title)

        project_count = 0
        if category_info.projects:
            project_count += len(category_info.projects)
        if category_info.hidden_projects:
            project_count += len(category_info.hidden_projects)

        if not project_count and (
            config.hide_empty_categories
            or category == default_config.DEFAULT_OTHERS_CATEGORY_ID
        ):
            # only add if more than 0 projects
            continue

        toc_points.append(f"[{category_info.title}]({url})")
    toc_md += " | ".join(toc_points) + "\n\n"
    return toc_md


def generate_md(categories: OrderedDict, config: Dict, labels: list) -> str:
    full_markdown = ""
    project_count = 0
    category_count = 0
    stars_count = 0

    for category_name in categories:
        category = categories[category_name]
        if not config.hide_empty_categories or (
            category.projects or category.hidden_projects
        ):
            category_count += 1

        if category.projects:
            for project in category.projects:
                project_count += 1
                if project.star_count:
                    stars_count += project.star_count

        if category.hidden_projects:
            for project in category.hidden_projects:
                project_count += 1
                if project.star_count:
                    stars_count += project.star_count

    if category_count > 0:
        # do not count others as category
        category_count -= 1

    if config.markdown_header_file:
        if os.path.exists(config.markdown_header_file):
            with open(config.markdown_header_file, "r") as f:
                full_markdown += (
                    str(f.read()).format(
                        project_count=utils.simplify_number(project_count),
                        category_count=utils.simplify_number(category_count),
                        stars_count=utils.simplify_number(stars_count),
                    )
                    + "\n"
                )
        else:
            log.warning(
                "The markdown header file does not exist: "
                + os.path.abspath(config.markdown_header_file)
            )

    if config.generate_toc:
        if config.short_toc:
            full_markdown += generate_short_toc(categories, config)
        else:
            full_markdown += markdown_list.generate_toc(categories, config)

    if config.generate_legend:
        full_markdown += markdown_list.generate_legend(config, labels)

    for category in categories:
        category_info = categories[category]
        full_markdown += generate_category_gallery_md(category_info, config, labels)

    if config.markdown_footer_file:
        if os.path.exists(config.markdown_footer_file):
            with open(config.markdown_footer_file, "r") as f:
                full_markdown += str(f.read()).format(
                    project_count=utils.simplify_number(project_count),
                    category_count=utils.simplify_number(category_count),
                    stars_count=utils.simplify_number(stars_count),
                )
        else:
            log.warning(
                "The markdown footer file does not exist: "
                + os.path.abspath(config.markdown_footer_file)
            )
    return full_markdown


class MarkdownGalleryGenerator(BaseGenerator):
    @property
    def name(self) -> str:
        return "markdown-gallery"

    def write_output(
        self, categories: OrderedDict, projects: List[Dict], config: Dict, labels: list
    ) -> None:
        markdown = generate_md(categories=categories, config=config, labels=labels)

        changes_md = markdown_list.generate_changes_md(projects, config, labels)

        if config.projects_history_folder:
            changes_md_file_name = datetime.today().strftime("%Y-%m-%d") + "_changes.md"
            # write to history folder
            with open(
                os.path.join(config.projects_history_folder, changes_md_file_name), "w"
            ) as f:
                f.write(changes_md)

        # write changes to working directory
        with open(
            os.path.join(
                os.path.dirname(config.output_file), default_config.LATEST_CHANGES_FILE
            ),
            "w",
        ) as f:
            f.write(changes_md)

        # Write markdown to file
        with open(config.output_file, "w") as f:
            f.write(markdown)

        # Create mobile version with 1 column.
        if config.mobile_version:
            mobile_config = Dict(config)
            mobile_config.output_file = config.get(
                "mobile_output_file", "README-mobile.md"
            )
            mobile_config.projects_per_row = 1
            if "mobile_markdown_header_file" in config:
                mobile_config.markdown_header_file = config.mobile_markdown_header_file
            if "mobile_markdown_footer_file" in config:
                mobile_config.markdown_footer_file = config.mobile_markdown_footer_file

            mobile_markdown = generate_md(
                categories=categories, config=mobile_config, labels=labels
            )

            # Write mobile markdown to file
            with open(mobile_config.output_file, "w") as f:
                f.write(mobile_markdown)
