from types import SimpleNamespace
import logging as logging
import itertools


def permutations(outer_key, outer, inner_key, inner):
    """
    return a list of SimpleNameSapce(step:, phase:) objects
    create product() of tuples and convert product() to list of dictionary{step:, phase:}
    outer is a tupple of steps
    inner is a tuple of phases
    outer_key and inner_key are the labels to be applied to the values in the tuples
    """
    logger = logging.getLogger(__name__)
    # TODO simplify this with a nested set of for loops
    # TODO tthis approach is techinically interesting and a great ref for future work
    # create a list of 4-tuples (label:value, label2:value2)
    all_phases = [
        list(item) for item in itertools.product(outer_key, outer, inner_key, inner)
    ]
    # convert [4-tubles] to [2-key dictionaries]
    logger.debug([type(item) for item in all_phases])
    all_phases_dict = [
        dict([(item[0], item[1]), (item[2], item[3])]) for item in all_phases
    ]
    # convert the [{step: phase:}] into [SimpleNamespace] objects
    all_phases = [SimpleNamespace(**one_step) for one_step in all_phases_dict]
    return all_phases
