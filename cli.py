import time
from math import ceil
from os.path import join

import click
import cv2
import imageio
import numpy as np
import scipy.ndimage as ndi
import skimage
from skimage.morphology import medial_axis, skeletonize
from skan import skeleton_to_csgraph

import thresholding
from options import AnalysisOptions
from results import AnalysisResult
from utils import write_results


def process(options: AnalysisOptions) -> AnalysisResult:
    output_prefix = join(options.output_directory, options.input_stem)
    print(f"Extracting traits from '{options.input_name}'")

    # read grayscale image
    gray_image = imageio.imread(options.input_file, as_gray=True)
    if len(gray_image) == 0:
        raise ValueError(f"Image is empty: {options.input_name}")

    # read color image
    color_image = imageio.imread(options.input_file, as_gray=False)
    if len(color_image) == 0:
        raise ValueError(f"Image is empty: {options.input_name}")

    # binary threshold
    masked_image = thresholding.binary_threshold(gray_image.astype(np.uint8))
    imageio.imwrite(f"{output_prefix}.mask.png", skimage.img_as_uint(masked_image))

    # edge detection
    print(f"Finding edges")
    edges_image = cv2.Canny(color_image, 100, 200)
    cv2.imwrite(f"{output_prefix}.edges.png", edges_image)

    # pad border
    print(f"Padding border")
    border_image = masked_image.copy()
    border_image[[0], :] = [255]
    border_image[[-1], :] = [255]
    border_image[:, [0]] = [255]
    border_image[:, [-1]] = [255]
    cv2.imwrite(f"{output_prefix}.border.png", skimage.img_as_uint(border_image))

    # invert image
    print(f"Inverting")
    inverted_image = (255 - border_image)
    cv2.imwrite(f"{output_prefix}.inverted.png", skimage.img_as_uint(inverted_image))

    # dilate image
    print(f"Dilating")
    kernel = np.ones((options.kernel_size, options.kernel_size), np.uint8)
    dilated_image = cv2.dilate(inverted_image, kernel, iterations=1)
    cv2.imwrite(f"{output_prefix}.dilated.png", dilated_image)

    # component labeling
    print(f"Finding connected components")
    num_labels, labels_image, stats, centroids = cv2.connectedComponentsWithStats(dilated_image)
    print(f"Found {num_labels} components")
    label_hue = np.uint8(179 * labels_image / np.max(labels_image))
    blank_ch = 255 * np.ones_like(label_hue)
    labeled_img = cv2.merge([label_hue, blank_ch, blank_ch])
    labeled_img = cv2.cvtColor(labeled_img, cv2.COLOR_HSV2BGR)
    labeled_img[label_hue == 0] = 0
    cv2.imwrite(f"{output_prefix}.labeled.png", labeled_img)

    # select largest component as plant
    sizes = stats[:, -1]
    max_label = 1
    max_size = sizes[1]
    for i in range(2, num_labels):
        if sizes[i] > max_size:
            max_label = i
            max_size = sizes[i]
    largest_comp_image = np.zeros(labels_image.shape)
    largest_comp_image[labels_image == max_label] = 255
    cv2.imwrite(f"{output_prefix}.largest.png", largest_comp_image)

    # get medial axis
    largest_comp_image[labels_image == max_label] = 1
    medial_image, distance = medial_axis(largest_comp_image, return_distance=True)
    cv2.imwrite(f"{output_prefix}.medial.png", skimage.img_as_uint(medial_image))

    # get skeleton (Lee 94)
    skeleton_image = skeletonize(largest_comp_image)
    cv2.imwrite(f"{output_prefix}.skeleton.png", skimage.img_as_uint(skeleton_image))

    # find branch points
    # (referenced from https://stackoverflow.com/a/67129378/6514033)
    # branch_points = np.zeros_like(skeleton_image, dtype=bool)
    # elements = list()
    # elements.append(np.array([[0, 1, 0], [1, 1, 1], [0, 0, 0]]))
    # elements.append(np.array([[1, 0, 1], [0, 1, 0], [1, 0, 0]]))
    # elements.append(np.array([[1, 0, 1], [0, 1, 0], [0, 1, 0]]))
    # elements.append(np.array([[0, 1, 0], [1, 1, 0], [0, 0, 1]]))
    # elements.append(np.array([[0, 0, 1], [1, 1, 1], [0, 1, 0]]))
    # elements = [np.rot90(elements[i], k=j) for i in range(5) for j in range(4)]
    # elements.append(np.array([[0, 1, 0], [1, 1, 1], [0, 1, 0]]))
    # elements.append(np.array([[1, 0, 1], [0, 1, 0], [1, 0, 1]]))
    # for element in elements: branch_points |= ndi.binary_hit_or_miss(skeleton_image, element)
    # cv2.imwrite(f"{output_prefix}.branchpts.png", branch_points.astype(float))

    # find branch points
    _, _, degrees = skeleton_to_csgraph(skeleton_image)
    branch_points = degrees > 2
    cv2.imwrite(f"{output_prefix}.branchpts.png", skimage.img_as_uint(branch_points))

    # find end points
    end_points = np.zeros_like(skeleton_image, dtype=bool)
    elements = list()
    elements.append(np.array([[0, 1, 0], [0, 1, 0], [0, 0, 0]]))
    elements.append(np.array([[1, 0, 0], [0, 1, 0], [0, 0, 0]]))
    elements = [np.rot90(elements[i], k=j) for i in range(2) for j in range(4)]
    for element in elements: end_points |= ndi.binary_hit_or_miss(skeleton_image, element)
    cv2.imwrite(f"{output_prefix}.endpts.png", skimage.img_as_uint(end_points))

    # find area, length, max height/width, number of branch/end points
    area = stats[max_label, cv2.CC_STAT_AREA]
    width = stats[max_label, cv2.CC_STAT_WIDTH]
    height = stats[max_label, cv2.CC_STAT_HEIGHT]
    length = int(np.sum(skeleton_image == 1))
    branch_points = int(np.sum(branch_points == 1))
    end_points = int(np.sum(end_points == 1))

    # print and return results
    print(f"Area: {area}")
    print(f"Width: {width}")
    print(f"Height: {height}")
    print(f"Length: {length}")
    print(f"Branch points: {branch_points}")
    print(f"End points: {end_points}")
    return AnalysisResult(name=options.input_name, area=area, width=width, height=height, length=length, branch_points=branch_points, end_points=end_points)


@click.command()
@click.argument('input_file')
@click.option('-o', '--output_directory', required=False, type=str, default='')
@click.option('-k', '--kernel_size', required=False, type=int, default=1)
def cli(input_file, output_directory, kernel_size):
    print(f"Starting")
    start = time.time()
    options = AnalysisOptions(input_file=input_file, output_directory=output_directory, kernel_size=kernel_size)

    print(f"Analyzing image")
    result = process(options)

    print(f"Writing results to file")
    write_results(options, [result])

    duration = ceil((time.time() - start))
    print(f"Finished in {duration} seconds.")


if __name__ == '__main__':
    cli()
