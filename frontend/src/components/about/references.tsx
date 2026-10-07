import type { ReactNode } from "react";

/**
 * Every work cited in the About chapters, keyed by a stable slug.
 *
 * `short` is what a citation prints inline, so a reader sees who said it
 * without leaving the sentence; the full entry is on /about/references.
 * Citing a slug that is not here fails the build's type check (`RefId`).
 */
interface Reference {
  short: string;
  entry: ReactNode;
}

const J = ({ children }: { children: ReactNode }) => <em>{children}</em>;

export const REFERENCES = {
  ansolabehere2003: {
    short: "Ansolabehere et al. 2003",
    entry: (
      <>
        Ansolabehere, S., de Figueiredo, J. M., &amp; Snyder, J. M. (2003). Why Is There So Little
        Money in U.S. Politics? <J>Journal of Economic Perspectives</J>, 17(1), 105–130.
        doi:10.1257/089533003321164976
      </>
    ),
  },
  ansolabehere2022: {
    short: "Ansolabehere & Kuriwaki 2022",
    entry: (
      <>
        Ansolabehere, S. &amp; Kuriwaki, S. (2022). Congressional Representation: Accountability
        from the Constituent&apos;s Perspective. <J>American Journal of Political Science</J>,
        66(1), 123–139.
      </>
    ),
  },
  bafumi2010: {
    short: "Bafumi & Herron 2010",
    entry: (
      <>
        Bafumi, J. &amp; Herron, M. C. (2010). Leapfrog Representation and Extremism: A Study of
        American Voters and Their Members in Congress. <J>American Political Science Review</J>,
        104(3), 519–542.
      </>
    ),
  },
  barro1992: {
    short: "Barro & Sala-i-Martin 1992",
    entry: (
      <>
        Barro, R. J. &amp; Sala-i-Martin, X. (1992). Convergence.{" "}
        <J>Journal of Political Economy</J>, 100(2), 223–251.
      </>
    ),
  },
  baumol1986: {
    short: "Baumol 1986",
    entry: (
      <>
        Baumol, W. J. (1986). Productivity Growth, Convergence, and Welfare: What the Long-Run Data
        Show. <J>American Economic Review</J>, 76(5), 1072–1085.
      </>
    ),
  },
  baumgartner1993: {
    short: "Baumgartner & Jones 1993",
    entry: (
      <>
        Baumgartner, F. R. &amp; Jones, B. D. (1993).{" "}
        <J>Agendas and Instability in American Politics</J>. University of Chicago Press.
      </>
    ),
  },
  bengio2003: {
    short: "Bengio et al. 2003",
    entry: (
      <>
        Bengio, Y., Ducharme, R., Vincent, P., &amp; Jauvin, C. (2003). A Neural Probabilistic
        Language Model. <J>Journal of Machine Learning Research</J>, 3, 1137–1155.
      </>
    ),
  },
  blinder2016: {
    short: "Blinder & Watson 2016",
    entry: (
      <>
        Blinder, A. S. &amp; Watson, M. W. (2016). Presidents and the U.S. Economy: An Econometric
        Exploration. <J>American Economic Review</J>, 106(4), 1015–1045.
      </>
    ),
  },
  bolt2024: {
    short: "Bolt & van Zanden 2024",
    entry: (
      <>
        Bolt, J. &amp; van Zanden, J. L. (2024). Maddison-style estimates of the evolution of the
        world economy: A new 2023 update. <J>Journal of Economic Surveys</J>.
      </>
    ),
  },
  bonica2014: {
    short: "Bonica 2014",
    entry: (
      <>
        Bonica, A. (2014). Mapping the Ideological Marketplace.{" "}
        <J>American Journal of Political Science</J>, 58(2), 367–386. doi:10.1111/ajps.12062
      </>
    ),
  },
  brin1998: {
    short: "Brin & Page 1998",
    entry: (
      <>
        Brin, S. &amp; Page, L. (1998). The Anatomy of a Large-Scale Hypertextual Web Search Engine.{" "}
        <J>Proceedings of the 7th International World Wide Web Conference</J>, 107–117.
      </>
    ),
  },
  canes2002: {
    short: "Canes-Wrone et al. 2002",
    entry: (
      <>
        Canes-Wrone, B., Brady, D. W., &amp; Cogan, J. F. (2002). Out of Step, Out of Office:
        Electoral Accountability and House Members&apos; Voting.{" "}
        <J>American Political Science Review</J>, 96(1), 127–140.
      </>
    ),
  },
  carson2010: {
    short: "Carson et al. 2010",
    entry: (
      <>
        Carson, J. L., Koger, G., Lebo, M. J., &amp; Young, E. (2010). The Electoral Costs of Party
        Loyalty in Congress. <J>American Journal of Political Science</J>, 54(3), 598–616.
        doi:10.1111/j.1540-5907.2010.00449.x
      </>
    ),
  },
  clinton2004: {
    short: "Clinton, Jackman & Rivers 2004",
    entry: (
      <>
        Clinton, J., Jackman, S., &amp; Rivers, D. (2004). The Statistical Analysis of Roll Call
        Data. <J>American Political Science Review</J>, 98(2), 355–370.
        doi:10.1017/S0003055404001194
      </>
    ),
  },
  clinton2006: {
    short: "Clinton 2006",
    entry: (
      <>
        Clinton, J. D. (2006). Representation in Congress: Constituents and Roll Calls in the 106th
        House. <J>Journal of Politics</J>, 68(2), 397–409.
      </>
    ),
  },
  cormack2009: {
    short: "Cormack et al. 2009",
    entry: (
      <>
        Cormack, G. V., Clarke, C. L. A., &amp; Büttcher, S. (2009). Reciprocal Rank Fusion
        Outperforms Condorcet and Individual Rank Learning Methods. <J>Proceedings of SIGIR 2009</J>
        , 758–759.
      </>
    ),
  },
  cover1967: {
    short: "Cover & Hart 1967",
    entry: (
      <>
        Cover, T. &amp; Hart, P. (1967). Nearest Neighbor Pattern Classification.{" "}
        <J>IEEE Transactions on Information Theory</J>, 13(1), 21–27. doi:10.1109/TIT.1967.1053964
      </>
    ),
  },
  donovan2020: {
    short: "Donovan et al. 2020",
    entry: (
      <>
        Donovan, K., Kellstedt, P. M., Key, E. M. &amp; Lebo, M. J. (2020). Motivated Reasoning,
        Public Opinion, and Presidential Approval. <J>Political Behavior</J>, 42(4), 1201–1221.
      </>
    ),
  },
  efron1975: {
    short: "Efron & Morris 1975",
    entry: (
      <>
        Efron, B. &amp; Morris, C. (1975). Data Analysis Using Stein&apos;s Estimator and Its
        Generalizations. <J>Journal of the American Statistical Association</J>, 70(350), 311–319.
        doi:10.2307/2285814
      </>
    ),
  },
  fenno1978: {
    short: "Fenno 1978",
    entry: (
      <>
        Fenno, R. F. (1978). <J>Home Style: House Members in Their Districts</J>. Little, Brown.
      </>
    ),
  },
  gerganov2023: {
    short: "Gerganov 2023",
    entry: (
      <>
        Gerganov, G. (2023). llama.cpp: Inference of LLaMA model in pure C/C++. GitHub.
        github.com/ggerganov/llama.cpp
      </>
    ),
  },
  grimmer2013: {
    short: "Grimmer & Stewart 2013",
    entry: (
      <>
        Grimmer, J. &amp; Stewart, B. M. (2013). Text as Data: The Promise and Pitfalls of Automatic
        Content Analysis Methods for Political Texts. <J>Political Analysis</J>, 21(3), 267–297.
        doi:10.1093/pan/mps028
      </>
    ),
  },
  harbridge2011: {
    short: "Harbridge & Malhotra 2011",
    entry: (
      <>
        Harbridge, L. &amp; Malhotra, N. (2011). Electoral Incentives and Partisan Conflict in
        Congress: Evidence from Survey Experiments. <J>American Journal of Political Science</J>,
        55(3), 494–510.
      </>
    ),
  },
  harbridgeyong2023: {
    short: "Harbridge-Yong et al. 2023",
    entry: (
      <>
        Harbridge-Yong, L., Volden, C., &amp; Wiseman, A. E. (2023). The Bipartisan Path to
        Effective Lawmaking. <J>Journal of Politics</J>, 85(3).
      </>
    ),
  },
  jacobson2019: {
    short: "Jacobson 2019",
    entry: (
      <>
        Jacobson, G. C. (2019). <J>Presidents and Parties in the Public Mind</J>. University of
        Chicago Press.
      </>
    ),
  },
  jurafsky2023: {
    short: "Jurafsky & Martin 2023",
    entry: (
      <>
        Jurafsky, D. &amp; Martin, J. H. (2023). <J>Speech and Language Processing</J> (3rd ed.
        draft). Stanford University.
      </>
    ),
  },
  karpukhin2020: {
    short: "Karpukhin et al. 2020",
    entry: (
      <>
        Karpukhin, V., Oguz, B., Min, S., Lewis, P., Wu, L., Edunov, S., Chen, D., &amp; Yih, W.
        (2020). Dense Passage Retrieval for Open-Domain Question Answering.{" "}
        <J>Proceedings of EMNLP 2020</J>, 6769–6781. doi:10.18653/v1/2020.emnlp-main.550
      </>
    ),
  },
  kirkland2017: {
    short: "Kirkland & Slapin 2017",
    entry: (
      <>
        Kirkland, J. H. &amp; Slapin, J. B. (2017). Ideology and Strategic Party Disloyalty in the
        US House of Representatives. <J>Electoral Studies</J>, 49.
      </>
    ),
  },
  laver2000: {
    short: "Laver & Garry 2000",
    entry: (
      <>
        Laver, M. &amp; Garry, J. (2000). Estimating Policy Positions from Political Texts.{" "}
        <J>American Journal of Political Science</J>, 44(3), 619–634. doi:10.2307/2669268
      </>
    ),
  },
  lewis2020: {
    short: "Lewis et al. 2020",
    entry: (
      <>
        Lewis, P., Perez, E., Piktus, A., Petroni, F., Karpukhin, V., Goyal, N., Küttler, H., Lewis,
        M., Yih, W., Rocktäschel, T., Riedel, S., &amp; Kiela, D. (2020). Retrieval-Augmented
        Generation for Knowledge-Intensive NLP Tasks. <J>Proceedings of NeurIPS 2020</J>.
        arXiv:2005.11401
      </>
    ),
  },
  lin1992: {
    short: "Lin 1992",
    entry: (
      <>
        Lin, L.-J. (1992). Self-improving reactive agents based on reinforcement learning, planning
        and teaching. <J>Machine Learning</J>, 8(3–4), 293–321. doi:10.1007/BF00992699
      </>
    ),
  },
  manning2008: {
    short: "Manning et al. 2008",
    entry: (
      <>
        Manning, C. D., Raghavan, P., &amp; Schütze, H. (2008).{" "}
        <J>Introduction to Information Retrieval</J>. Cambridge University Press. Ch. 14: Vector
        Space Classification.
      </>
    ),
  },
  mccarty2006: {
    short: "McCarty, Poole & Rosenthal 2006",
    entry: (
      <>
        McCarty, N., Poole, K. T. &amp; Rosenthal, H. (2006).{" "}
        <J>Polarized America: The Dance of Ideology and Unequal Riches</J>. MIT Press.
      </>
    ),
  },
  minaee2021: {
    short: "Minaee et al. 2021",
    entry: (
      <>
        Minaee, S., Kalchbrenner, N., Cambria, E., Nikzad, N., Chenaghlu, M., &amp; Gao, J. (2021).
        Deep Learning-Based Text Classification: A Comprehensive Review.{" "}
        <J>ACM Computing Surveys</J>, 54(3), 1–40. doi:10.1145/3439726
      </>
    ),
  },
  nokken2004: {
    short: "Nokken & Poole 2004",
    entry: (
      <>
        Nokken, T. P. &amp; Poole, K. T. (2004). Congressional Party Defection in American History.{" "}
        <J>Legislative Studies Quarterly</J>, 29(4), 545–568.
      </>
    ),
  },
  patterson2021: {
    short: "Patterson et al. 2021",
    entry: (
      <>
        Patterson, D., Gonzalez, J., Le, Q., Liang, C., Munguia, L.-M., Rothchild, D., So, D.,
        Texier, M., &amp; Dean, J. (2021). Carbon Emissions and Large Neural Network Training.{" "}
        <J>arXiv:2104.10350</J>.
      </>
    ),
  },
  poole1985: {
    short: "Poole & Rosenthal 1985",
    entry: (
      <>
        Poole, K. T. &amp; Rosenthal, H. (1985). A Spatial Model for Legislative Roll Call Analysis.{" "}
        <J>American Journal of Political Science</J>, 29(2), 357–384. doi:10.2307/2111172
      </>
    ),
  },
  reimers2019: {
    short: "Reimers & Gurevych 2019",
    entry: (
      <>
        Reimers, N. &amp; Gurevych, I. (2019). Sentence-BERT: Sentence Embeddings using Siamese
        BERT-Networks. <J>Proceedings of EMNLP-IJCNLP 2019</J>, 3982–3992. doi:10.18653/v1/D19-1410
      </>
    ),
  },
  rhoades1993: {
    short: "Rhoades 1993",
    entry: (
      <>
        Rhoades, S. A. (1993). The Herfindahl-Hirschman Index. <J>Federal Reserve Bulletin</J>, 79,
        188–189.
      </>
    ),
  },
  robertson2009: {
    short: "Robertson & Zaragoza 2009",
    entry: (
      <>
        Robertson, S. &amp; Zaragoza, H. (2009). The Probabilistic Relevance Framework: BM25 and
        Beyond. <J>Foundations and Trends in Information Retrieval</J>, 3(4), 333–389.
      </>
    ),
  },
  romer1989: {
    short: "Romer 1989",
    entry: (
      <>
        Romer, C. D. (1989). The Prewar Business Cycle Reconsidered: New Estimates of Gross National
        Product, 1869–1908. <J>Journal of Political Economy</J>, 97(1), 1–37.
      </>
    ),
  },
  sen1968: {
    short: "Sen 1968",
    entry: (
      <>
        Sen, P. K. (1968). Estimates of the Regression Coefficient Based on Kendall&apos;s Tau.{" "}
        <J>Journal of the American Statistical Association</J>, 63(324), 1379–1389.
      </>
    ),
  },
  snell2017: {
    short: "Snell et al. 2017",
    entry: (
      <>
        Snell, J., Swersky, K., &amp; Zemel, R. (2017). Prototypical Networks for Few-Shot Learning.{" "}
        <J>Proceedings of NeurIPS 2017</J>, 4077–4087. arXiv:1703.05175
      </>
    ),
  },
  snyder2000: {
    short: "Snyder & Groseclose 2000",
    entry: (
      <>
        Snyder, J. M. &amp; Groseclose, T. (2000). Estimating Party Influence in Congressional
        Roll-Call Voting. <J>American Journal of Political Science</J>, 44(2), 193–211.
        doi:10.2307/2669305
      </>
    ),
  },
  stratmann2005: {
    short: "Stratmann 2005",
    entry: (
      <>
        Stratmann, T. (2005). Some Talk: Money in Politics. A (Partial) Review of the Literature.{" "}
        <J>Public Choice</J>, 124(1–2), 135–156. doi:10.1007/s11127-005-4750-3
      </>
    ),
  },
  tauberer2012: {
    short: "Tauberer 2012",
    entry: (
      <>
        Tauberer, J. (2012). <J>Open Government Data: The Book</J>. GovTrack.us methodology for
        ideology and leadership scoring via cosponsorship analysis. govtrack.us/about/analysis
      </>
    ),
  },
  volden2014: {
    short: "Volden & Wiseman 2014",
    entry: (
      <>
        Volden, C. &amp; Wiseman, A. E. (2014).{" "}
        <J>Legislative Effectiveness in the United States Congress: The Lawmakers</J>. Cambridge
        University Press.
      </>
    ),
  },
  wang2020: {
    short: "Wang et al. 2020",
    entry: (
      <>
        Wang, W., Wei, F., Dong, L., Bao, H., Yang, N., &amp; Zhou, M. (2020). MiniLM: Deep
        Self-Attention Distillation for Task-Agnostic Compression of Pre-Trained Transformers.{" "}
        <J>Proceedings of NeurIPS 2020</J>. arXiv:2002.10957
      </>
    ),
  },
  yarowsky1995: {
    short: "Yarowsky 1995",
    entry: (
      <>
        Yarowsky, D. (1995). Unsupervised Word Sense Disambiguation Rivaling Supervised Methods.{" "}
        <J>Proceedings of ACL 1995</J>, 189–196. doi:10.3115/981658.981684
      </>
    ),
  },
  yin2019: {
    short: "Yin, Hay & Roth 2019",
    entry: (
      <>
        Yin, W., Hay, J., &amp; Roth, D. (2019). Benchmarking Zero-shot Text Classification:
        Datasets, Evaluation and Entailment Approach. <J>Proceedings of EMNLP 2019</J>, 3914–3923.
        doi:10.18653/v1/D19-1404
      </>
    ),
  },
} satisfies Record<string, Reference>;

export type RefId = keyof typeof REFERENCES;

/**
 * The old single page numbered its references; its `#ref-N` anchors are
 * forwarded to the slug that entry now lives under. Four numbers are gone:
 * 2 and 3 with the feature they supported (campaign-promise extraction),
 * 15 and 31 with the claims they were cited for (that the language model
 * does multi-step reasoning to build issues — it only locates quotes now;
 * and that bill content outranks roll calls for party alignment — the
 * reverse has been true since 2026-06). A missing number stays on /about.
 */
export const LEGACY_REF_NUMBERS: Readonly<Record<string, RefId>> = {
  "1": "bonica2014",
  "4": "carson2010",
  "5": "stratmann2005",
  "6": "rhoades1993",
  "7": "reimers2019",
  "8": "wang2020",
  "9": "cover1967",
  "10": "lin1992",
  "11": "karpukhin2020",
  "12": "jurafsky2023",
  "13": "minaee2021",
  "14": "snell2017",
  "16": "gerganov2023",
  "17": "patterson2021",
  "18": "ansolabehere2003",
  "19": "efron1975",
  "20": "poole1985",
  "21": "clinton2004",
  "22": "manning2008",
  "23": "laver2000",
  "24": "snyder2000",
  "25": "yarowsky1995",
  "26": "lewis2020",
  "27": "grimmer2013",
  "28": "baumgartner1993",
  "29": "bengio2003",
  "30": "yin2019",
  "32": "brin1998",
  "33": "tauberer2012",
  "34": "volden2014",
  "35": "canes2002",
  "36": "nokken2004",
};
